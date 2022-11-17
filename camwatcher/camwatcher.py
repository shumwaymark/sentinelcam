"""camwatcher: A component of the SentinelCam data layer. 
Proivides subscriber services for log and image publishing from
outpost nodes. Drives a dispatcher to trigger other functionality.

Copyright (c) 2021 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import asyncio
import json
import logging
import logging.handlers
import multiprocessing
import queue
import threading
import numpy as np
import imagezmq
import zmq
from time import sleep
from datetime import datetime
from multiprocessing import sharedctypes
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

# multiprocessing class implementing a sub-process image writer
class VideoStreamWriter:

    def __init__(self, event, host, port, node, view):
        self.nodeView = (node, view)
        self.timer = None
        self._writeVideo = multiprocessing.Value('i', 1)
        self._eventID = sharedctypes.RawArray('c', bytes(event,'utf-8'))
        self._newEventID = multiprocessing.Event()
        self._newEventID.set()
        self.process = multiprocessing.Process(target=self._capture_video, args=(
            self._writeVideo, self._newEventID, self._eventID, host, port, view, cfg['outdir']))
        self.process.start()
        logging.debug("Video writer started for {} event {} pid {}".format((node,view),
            str(self._eventID.value,'utf-8'), self.process.pid))
    
    def _set_datedir(self, dir, date):
        path = os.path.join(dir, date)
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
        return path

    def _capture_video(self, writeVideo, newEvent, eventID, host, port, view, outdir):
        publisher = "tcp://{}:{}".format(host, port)
        # start image subscription thread and begin frame capture loop
        receiver = VideoStreamSubscriber(publisher, view)
        try:
            while writeVideo.value:
                dt, frame = receiver.receive()
                if newEvent.is_set():
                    evt = str(eventID.value,'utf-8')
                    date_directory = self._set_datedir(outdir, dt[:10])
                    newEvent.clear()
                if len(dt) == 19:
                    dt = dt + ".000000"
                jpegframe = "{}_{}_{}.jpg".format(
                    evt, dt[:10], dt[11:].replace(':','.'))
                jpegfile = os.path.join(date_directory, jpegframe)
                # write the image file to disk 
                with open(jpegfile,"wb") as f:
                    f.write(frame)
        except (KeyboardInterrupt, SystemExit):
            logging.info('VideoStreamWriter exiting')
        except Exception as ex:
            logging.error("VideoStreamWriter exception: " + str(ex))
        finally:
            receiver.close()

    def update_eventID(self, event):
        if self.timer:
            self.timer.cancel()
        self._eventID.value = bytes(event,'utf-8')
        self._newEventID.set()
    
    def get_eventID(self):
        return str(self._eventID.value, 'utf-8')

    def close(self):
        self._writeVideo.value = 0
        self.process.join()
        logging.debug("CamWatcher closed capture process {} from {} event {}".format(
            self.process.pid, self.nodeView, self._eventID.value))

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
    
    def _setindex(self, nodeView, evt, timestamp, fps):
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
                index.write(','.join([nodeView[0], nodeView[1], timestamp, evt, str(int(fps)), 'trk']) + "\n")
        except Exception as ex:
            logging.error(f"CSVwriter failure updating index file: {str(ex)}")
        return date_directory

    def _run(self):
        logging.info(f"CSVwriter thread starting within {self._folder}")
        while not self._stop:
            if dbLogMsgs.empty():
                sleep(1)
                continue
            while not dbLogMsgs.empty():
                nodeView, ote = dbLogMsgs.get()
                try:
                    if ote["evt"] == 'start': 
                        f = open(os.path.join(self._setindex(
                            nodeView, ote['id'], ote['timestamp'], ote["fps"]), 
                            ote["id"] + '_trk.csv'), mode='wt')
                        f.write("timestamp,objid,classname,rect_x1,rect_y1,rect_x2,rect_y2\n") # write column headers
                        self._openfiles[ote["id"]] = f # add to list
                    elif ote["evt"] == 'trk':
                        self._openfiles[ote["id"]].write(','.join([
                            ote['timestamp'],
                            str(ote['obj']),
                            str(ote['clas']),
                            str(ote['rect'][0]), 
                            str(ote['rect'][1]), 
                            str(ote['rect'][2]), 
                            str(ote['rect'][3])
                            ]) + "\n" )
                    elif ote["evt"] == 'end':
                        logging.debug("CSVwriter closing file for {}".format(ote['id']))
                        self._openfiles[ote["id"]].close() # close file
                        del self._openfiles[ote["id"]] # remove from list
                    else:
                        logging.warning("Tracking event {} from {} ignored by CSVwriter".format(ote["evt"], nodeView))
                except KeyError as keyval:
                    logging.error(f"CSVWriter event {ote['id']} not found in list of writers?, KeyError: {keyval}")
                except Exception as ex:
                    logging.error(f"CSVwriter thread unhandled exception: {str(ex)}")
                dbLogMsgs.task_done()
        logging.debug("CSVwriter closing")
        for f in self._openfiles:
            f.close()

    def close(self):
        self._stop = True
        self._thread.join()

async def control_loop(loggers):
    rep = ctx.socket(zmq.REP)
    rep.bind(f"tcp://*:{cfg['control_port']}")
    logging.info("CamWatcher control port ready.")
    while True:
        msg = await rep.recv()
        msg = msg.decode("ascii").split('|')
        result = 'OK'
        #command = msg[0]
        # CameraUp is only supported command, so must be a new outpost
        # logpublisher handoff as json-encoded Dict in the second field
        try:
            outpost = json.loads(msg[1])
            if not outpost['node'] in watchList: 
                loggers.connect(f"tcp://{outpost['host']}:{outpost['log']}")
                watchList[outpost['node']] = outpost
                print_outpost(outpost)
            else:
                logging.info(f"CamWatcher already connected with {outpost['node']}")
        except ValueError as ex:
            result = 'REJECT'
            logging.error(f"JSON exception '{str(ex)}' decoding camera handoff message: '{msg[1]}'")
        except KeyError as keyval:
            result = 'REJECT'
            logging.error(f"Invalid camera handoff, missing '{keyval}' in message: '{msg[1]}'")
        except Exception as ex:  
            result = 'REJECT'
            logging.error(f"CamWatcher subscription failure for '{outpost['node']}': {str(ex)}")
        logging.debug(f"CamWatcher control port reply {result}")
        await rep.send(result.encode("ascii"))
            
def print_outpost(outpost):
    print(f"New outpost registered. host: {outpost['host']} node: {outpost['node']}")

def dispatch_logger(topics, msg):
    if topics[1] == 'ERROR':
        logging.error(f"[{topics[0]}]{msg}") 
    elif topics[1] == 'WARNING':
        logging.warning(f"[{topics[0]}]{msg}") 
    elif topics[1] == 'CRITICAL':
        logging.critical(f"[{topics[0]}]{msg}") 
    elif topics[1] == 'INFO':
        logging.info(f"[{topics[0]}]{msg}") 
    elif topics[1] == 'DEBUG':
        logging.debug(f"[{topics[0]}]{msg}") 
    else:
        logging.error(f"[{'.'.join(topics)}]{'*** Foreign TOPIC *** ' + msg}") 

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
                    logging.debug(message)
                elif category == 'fps':  # Outpost image publishing heartbeat
                    logging.info(f"Outpost health '{topics[0]}' {message[3:]}")
                elif category == 'Exi':  # this is the "Exit" message from an imagenode
                    dispatch_logger(topics, message)
                else:
                    logging.warning("Unknown message category {} from {}".format(
                        message[:3], ".".join(topics)))
            else:
                dispatch_logger(topics, message)
        else:
            await asyncio.sleep(1)

def shutdown_writer(nodeView, evt):
    if writerList[nodeView].get_eventID() == evt:
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
                writerList[nodeView].update_eventID(eventID)
            else:
                writerList[nodeView] = VideoStreamWriter(eventID, 
                    outpost['host'], outpost['video'], node, view) 
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
    log.setLevel(logging.INFO)
    return log

if __name__ == '__main__' :
    asyncio.run(main())
