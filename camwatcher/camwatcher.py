"""camwatcher: A component of the SentinelCam data layer. 
Proivides subscriber services for log and video publishing from
outpost nodes. Drives a dispatcher to trigger other functionality.

Copyright (c) 2021 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import asyncio
import json
import logging
import logging.handlers
import queue
import threading
import numpy as np
import imagezmq
import zmq
from time import sleep
from datetime import datetime
from multiprocessing import Process
from multiprocessing import Value
from zmq.asyncio import Context as AsyncContext

cfg = {'control_port': 5566, # bind on this socket as REP for control commands
       'outdir': '/mnt/usb1/imagedata/video',
       'csvdir': '/mnt/usb1/imagedata/camwatcher' } 

log = None
ctx = AsyncContext.instance()
watchList = {}                 # subscriptions to "outpost" log publishers
writerList = {}                # active VideoStreamWriters
dbLogMsgs = queue.Queue()      # log data content messages for DBMS writer 

# Helper class implementing an IO deamon thread as an image subscriber
class VideoStreamSubscriber:

    def __init__(self, publisher, view):
        self.publisher = publisher
        self.view = view
        self._stop = False
        self._data_ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=())
        self._thread.daemon = True
        self._thread.start()

    def receive(self, timeout=15.0):
        flag = self._data_ready.wait(timeout=timeout)
        if not flag:
            raise TimeoutError(
                "Timed out reading from publisher {}".format(self.publisher))
        self._data_ready.clear()
        return self._data

    def _run(self):
        receiver = imagezmq.ImageHub(self.publisher, REQ_REP=False)
        while not self._stop:
            imagedata = receiver.recv_jpg()
            if imagedata[0].split('|')[0].split(' ')[1] == self.view:
                self._data = (datetime.utcnow().isoformat(), imagedata[1])
                self._data_ready.set()
        receiver.close()

    def close(self):
        self._stop = True

# multiprocessing class implementing a sub-process video writer
class VideoStreamWriter:

    def __init__(self, event, host, port, node, view):
        self.nodeView = (node, view)
        self.event = event
        self.timer = None
        self._writeVideo = Value('i', 1)
        self.process = Process(target=self._capture_video, args=(
            self._writeVideo, host, port, node, view, cfg['outdir']))
        self.process.start()
        logging.debug("Video writer started for {} event {} pid {}".format((node,view),
            self.event, self.process.pid))
    
    def _capture_video(self, writeVideo, host, port, node, view, outdir):
        publisher = "tcp://{}:{}".format(host, port)
        date = datetime.utcnow().isoformat()[:10]
        date_directory = os.path.join(outdir, date)
        try:
            os.mkdir(date_directory)
        except FileExistsError:
            pass
        # start image subscription thread and begin video capture loop
        receiver = VideoStreamSubscriber(publisher, view)
        try:
            while writeVideo.value:
                dt, frame = receiver.receive()
                jpegframe = "{}_{}_{}.jpg".format(
                    self.event, dt[:10], dt[11:].replace(':','.'))
                jpegfile = os.path.join(date_directory, jpegframe)
                # write the image file to disk 
                with open(jpegfile,"wb") as f:
                    f.write(frame)
        except (KeyboardInterrupt, SystemExit):
            logging.info('VideoStreamWriter exiting')
        except Exception as ex:
            logging.error("VideoStreamWriter exception: " + ex)
        finally:
            receiver.close()

    def close(self):
        self._writeVideo.value = 0
        logging.debug("CamWatcher closing capture process {} from {} event {}".format(
            self.process.pid, self.nodeView, self.event))

# Disk I/O CSV writer thread
class CSVwriter:

    def __init__(self, dir):
        self._openfiles = {} # a list of open files by unique identifier
        self._folder = dir # top-level folder for CSV files
        self._today = None # cuurent date as 'YYYY-MM-DD'
        self._stop = False
        self._thread = threading.Thread(target=self._run, args=())
        self._thread.daemon = True
        self._thread.start()
    
    def _setindex(self, nodeView, evt, timestamp):
        today = timestamp[:10] 
        logging.debug("CSVwriter index setup " + evt)
        date_directory = os.path.join(self._folder, today)
        if today != self._today:
            logging.debug("Date folder selection: " + today)
            try:
                # if date value changes, insure folder exists 
                os.mkdir(date_directory) 
            except FileExistsError:
                pass
        # write an entry into the date folder index
        self._today = today
        try:
            with open(os.path.join(date_directory, 'camwatcher.csv'), mode='at') as index:
                index.write(','.join([nodeView[0], nodeView[1], timestamp, evt, 'trk']) + "\n")
        except Exception as ex:
            logging.error("CSVwriter failure updating index file: " + ex)
        return date_directory

    def _run(self):
        logging.debug("CSVwriter thread starting within " + self._folder)
        while not self._stop:
            if dbLogMsgs.empty():
                sleep(1)
                continue
            while not dbLogMsgs.empty():
                nodeView, ote = dbLogMsgs.get()
                try:
                    if ote["evt"] == 'start': 
                        f = open(os.path.join(self._setindex(nodeView, ote['id'], ote['timestamp']), ote["id"] + '_trk.csv'), mode='wt')
                        f.write("node,view,event,timestamp,objid,centroid_x,centroid_y\n") # write column headers
                        self._openfiles[ote["id"]] = f # add to list
                    elif ote["evt"] == 'trk':
                        self._openfiles[ote["id"]].write(','.join([
                            nodeView[0], 
                            nodeView[1],
                            ote["id"],
                            ote['timestamp'],
                            str(ote["obj"]),
                            str(ote['cent'][0]), 
                            str(ote['cent'][1])
                            ]) + "\n" )
                    elif ote["evt"] == 'end':
                        logging.debug("CSVwriter closing file for {}".format(ote['id']))
                        self._openfiles[ote["id"]].close() # close file
                        del self._openfiles[ote["id"]] # remove from list
                    else:
                        logging.warning("Tracking event {} from {} ignored by CSVwriter".format(ote["evt"], nodeView))
                except KeyError:
                    logging.error("CSVWriter event not found in list of writers: " + ote["id"])
                except Exception as ex:
                    logging.error("CSVwriter thread unhandled exception: " + ex)
                dbLogMsgs.task_done()
        logging.debug("CSVwriter closing")
        for f in self._openfiles:
            f.close()

    def close(self):
        self._stop = True

async def control_loop(loggers):
    rep = ctx.socket(zmq.REP)
    rep.bind(f"tcp://*:{cfg['control_port']}")
    while True:
        msg = await rep.recv()
        msg = msg.decode("ascii").split('|')
        result = 'OK'
        #command = msg[0]
        # CameraUp is only supported command, so must be a new outpost
        # logpublisher handoff as json-encoded Dict in the second field
        outpost = json.loads(msg[1]) # TODO handoff validation needed here
        if not outpost['node'] in watchList: 
            try:
                loggers.connect(f"tcp://{outpost['host']}:{outpost['log']}")
                watchList[outpost['node']] = outpost
                print_outpost(outpost)
            except Exception as ex:  
                result = 'FAILED'
                logging.error('CW subscription failure ' + ex)
        logging.debug("CW control port reply " + result)
        await rep.send(result.encode("ascii"))
            
def print_outpost(outpost):
    print('New outpost registered')
    print(f"host: {outpost['host']}")
    print(f"node: {outpost['node']}")
    print(f"view: {outpost['view']}")

def dispatch_logger(topics, msg):
    if topics[1] == 'ERROR':
        logging.error(f"|{'.'.join(topics)}|{msg}") 
    elif topics[1] == 'WARNING':
        logging.warning(f"|{'.'.join(topics)}|{msg}") 
    elif topics[1] == 'CRITICAL':
        logging.critical(f"|{'.'.join(topics)}|{msg}") 
    elif topics[1] == 'INFO':
        logging.info(f"|{'.'.join(topics)}|{msg}") 
    elif topics[1] == 'DEBUG':
        logging.debug(f"|{'.'.join(topics)}|{msg}") 

async def process_logs(loggers):
    while True:
        if len(watchList) > 0:
            topic, msg = await loggers.recv_multipart()
            topics = topic.decode('utf8').strip().split('.')
            message = msg.decode('ascii')
            # trim any trailing newline from log message
            if message.endswith('\n'): message = message[:-1] 
            if topics[1] == 'INFO': # node name is in topics[0]
                category = message[:3] 
                if category == 'ote':   # object tracking event 
                    await dispatch_ote(topics[0], message[3:])
                else:
                    logging.warning("Unknown message category ignored: " + ".".join(topics))
            else:
                dispatch_logger(topics, msg)
            logging.debug(message)
        else:
            await asyncio.sleep(1)

def shutdown_writer(nodeView, evt):
    if writerList[nodeView].event == evt:
        writerList[nodeView].close()
        del writerList[nodeView]
        logging.debug("Video capture shutdown for event {} from {}".format(evt,nodeView))
    else:
        logging.debug("Video capture shutdown event {} for {} ignored".format(evt,nodeView))
    
async def dispatch_ote(node, ote_data):
    ote = json.loads(ote_data)
    outpost = watchList[node] # details provided by the outpost node
    eventID = ote["id"]
    view = ote['view']
    ote["timestamp"] = datetime.utcnow().isoformat()
    nodeView = (node, view)
    ote2db = (nodeView, ote)
    if ote["evt"] == 'start':
        dbLogMsgs.put(ote2db)
        if nodeView in writerList:
            if writerList[nodeView].process.is_alive():
                # still running, update stored eventID and stay alive 
                writerList[nodeView].event = eventID 
                writerList[nodeView].timer.cancel() 
            else:
                writerList[nodeView] = VideoStreamWriter(eventID, 
                    outpost['host'], outpost['video'], node, view) 
                dbLogMsgs.put(ote2db)
        else:
            writerList[nodeView] = VideoStreamWriter(eventID, 
                outpost['host'], outpost['video'], node, view)
    elif ote["evt"] == 'trk':
        dbLogMsgs.put(ote2db)
    elif ote["evt"] == 'end':
        dbLogMsgs.put(ote2db)
        if nodeView in writerList:
            if writerList[nodeView].process.is_alive():
                # provide for 2 second grace period before termination
                timerhandle = asyncio.get_running_loop().call_later(2,
                    shutdown_writer, nodeView, eventID)
                writerList[nodeView].timer = timerhandle
            else:
                del writerList[nodeView]
    else:
        logging.warning('Unrecognized tracking event {}'.format(ote["evt"]))
    
async def main():
    log = start_logging()
    logsock = ctx.socket(zmq.SUB) # SUB socket for log publisher subscriptions
    logsock.subscribe(b'')
    csv = CSVwriter(cfg['csvdir'])
    try:
        await asyncio.gather(control_loop(logsock), process_logs(logsock))
    except (KeyboardInterrupt, SystemExit):
        log.warning('Ctrl-C was pressed or SIGTERM was received')
    except Exception as ex:  # traceback will appear in log
        log.exception('Unanticipated error with no Exception handler.')
    finally:
        csv.close()

def start_logging():
    log = logging.getLogger()
    handler = logging.handlers.RotatingFileHandler('camwatcher.log',
        maxBytes=1048576, backupCount=10)
    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    return log

if __name__ == '__main__' :
    asyncio.run(main())
