# camwatcher.py
import os
import sys
import socket
import traceback
import asyncio
import asyncpg
import json
import logging
import logging.handlers
import threading
import numpy as np
import imagezmq
import zmq
import cv2
from time import sleep
from datetime import datetime
#from imutils.video import VideoStream
#from sentinelcam.utils import FPS
from multiprocessing import Process
from multiprocessing import Value
from zmq.asyncio import Context as AsyncContext

cfg = {'control_port': 5566, # bind on this socket as REP for control commands
       'dbconn': 'postgresql://sentinelcam:sentinelcam@data1/sentinelcam', # DBMS connection string
       'outdir': '/mnt/usb1/imagedata/video' } 

log = None
ctx = AsyncContext.instance()
watchList = {}                 # subscriptions to "outpost" log publishers
writerList = {}                # active VideoStreamWriters

# Helper class implementing an IO deamon thread as an image subscriber
class VideoStreamSubscriber:

    def __init__(self, publisher, view):
        self.publisher = publisher
        self.view = view
        #self.velocity = FPS()
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
                self._data = imagedata
                self._data_ready.set()
            #self.velocity.update()
        receiver.close()

    def close(self):
        self._stop = True

# multiprocessing class implementing a sub-process video writer
class VideoStreamWriter:

    def __init__(self, event, host, port, node, view, fps, W, H):
        self.nodeView = (node, view)
        self.event = event
        self.timer = None
        self._writeVideo = Value('i', 1)
        self.process = Process(target=self._capture_video, args=(
            self._writeVideo, host, port, node, view, fps, (W,H), cfg['outdir']))
        self.process.start()
        logging.debug("Video writer started for {} event {} pid {}".format((node,view),
            self.event, self.process.pid))
    
    def _capture_video(self, writeVideo, host, port, node, view, fps, size, outdir):
        publisher = "tcp://{}:{}".format(host, port)
        date = datetime.now().isoformat()[:10]
        date_directory = os.path.join(outdir, date)
        try:
            os.mkdir(date_directory)
        except FileExistsError:
            pass
        count = 0
        #velocity = FPS()
        jpegbase = '_'.join([node, view, str(self.event).zfill(5)])
        # start image subscription thread and begin video capture loop
        receiver = VideoStreamSubscriber(publisher, view)
        try:
            while writeVideo.value:
                msg, frame = receiver.receive()
                #velocity.update()
                count += 1
                jpegframe = jpegbase + "_{}.jpg".format(str(count).zfill(10))
                    #receiver.velocity.fps(),
                    #velocity.fps()) 
                jpegfile = os.path.join(date_directory, jpegframe)
                # write the image file to disk 
                with open(jpegfile,"wb") as f:
                    f.write(frame)
        except (KeyboardInterrupt, SystemExit):
            print('Exiting now')
        except Exception as ex:
            print('Uncaught exception handler:')
            print('Traceback error:', ex)
            traceback.print_exc()
        finally:
            receiver.close()

    def close(self):
        self._writeVideo.value = 0
        logging.debug("CamWatcher closing capture process {} from {} event {}".format(
            self.process.pid, self.nodeView, self.event))

async def control_loop(loggers):
    rep = ctx.socket(zmq.REP)
    rep.bind(f"tcp://*:{cfg['control_port']}")
    while True:
        msg = await rep.recv()
        msg = msg.decode("ascii").split('|')
        result = 'OK'
        #command = msg[0]
        # CameraUp is only supported command, so must be a new ImageNode
        # logpublisher handoff as json-encoded Dict in the second field
        outpost = json.loads(msg[1]) # TODO handoff validation needed here
        if not outpost['node'] in watchList: 
            try:
                loggers.connect(f"tcp://{outpost['host']}:{outpost['log']}")
                watchList[outpost['node']] = outpost
                print_outpost(outpost)
            except Exception as ex:  
                result = 'FAILED'
                print('CW subscription failure ' + ex)
        print("CW sending " + result)
        rep.send(result.encode("ascii"))
            
def print_outpost(outpost):
    print('New outpost registered')
    print(f"host: {outpost['host']}")
    print(f"node: {outpost['node']}")
    print(f"log: {outpost['log']}")
    print(f"video: {outpost['video']}")
    for v in outpost['cams']: print(v, outpost['cams'][v])

async def process_logs(loggers, dbLogMsgs):
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
                    await dispatch_ote(dbLogMsgs, topics[0], message[3:])
                else:
                    print('{}|{}|{}'.format(datetime.now(),
                        ".".join(topics),message))
            else:
                print('{}|{}|{}'.format(datetime.now(),
                    ".".join(topics),message))
            logging.debug(message)
        else:
            await asyncio.sleep(1)

async def dbms_writer(dbLogMsgs):
    eventStart = {} # holds event starting timestamp by node/view 
    cam_event = """
       INSERT INTO cam_event (node_name, view_name, start_time, pipe_event, pipe_fps) 
            VALUES ($1, $2, $3, $4, $5) """
    cam_tracking = """
       INSERT INTO cam_tracking (node_name, view_name, start_time, pipe_event, 
                   object_time, object_tag, centroid_x, centroid_y) 
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8) """
    conn = await asyncpg.connect(dsn=cfg['dbconn'])
    while True:
        payload = await dbLogMsgs.get()
        nodeView = payload[0]
        ote = payload[1]
        if ote["evt"] == 'start':
            eventStart[nodeView] = datetime.now() 
            await conn.execute(cam_event,nodeView[0], nodeView[1],
                eventStart[nodeView], ote["id"], ote["fps"])
        elif ote["evt"] == 'trk':
            await conn.execute(cam_tracking,nodeView[0], nodeView[1],
                eventStart[nodeView], ote["id"], datetime.now(), ote["obj"],
                ote["cent"][0], ote["cent"][1])
        dbLogMsgs.task_done()

def shutdown_writer(nodeView, evt):
    if writerList[nodeView].event == evt:
        writerList[nodeView].close()
        del writerList[nodeView]
        logging.debug("Video capture shutdown for event {} from {}".format(evt,nodeView))
    else:
        logging.debug("Video capture shutdown event {} for {} ignored".format(evt,nodeView))
    
async def dispatch_ote(dbLogMsgs, node, ote_data):
    ote = json.loads(ote_data)
    outpost = watchList[node]
    eventID = ote["id"]
    view = ote['view']
    nodeView = (node, view)
    ote2db = (nodeView, ote)
    if ote["evt"] == 'start':
        if nodeView in writerList:
            if writerList[nodeView].process.is_alive():
                # still running, update stored eventID and stay alive 
                writerList[nodeView].event = eventID 
                writerList[nodeView].timer.cancel() 
            else:
                writerList[nodeView] = VideoStreamWriter(eventID, 
                    outpost['host'], outpost['video'], node, view, ote['fps'],
                    outpost['cams'][view][0],  # view width
                    outpost['cams'][view][1])  # view height
                await dbLogMsgs.put(ote2db)
        else:
            writerList[nodeView] = VideoStreamWriter(eventID, 
                outpost['host'], outpost['video'], node, view, ote['fps'],
                outpost['cams'][view][0],  # view width
                outpost['cams'][view][1])  # view height
            await dbLogMsgs.put(ote2db)
    elif ote["evt"] == 'trk':
        await dbLogMsgs.put(ote2db)
        logging.debug("Logging data {}".format(ote))
    elif ote["evt"] == 'end':
        if nodeView in writerList:
            if writerList[nodeView].process.is_alive():
                # provide for 2 second grace period before termination
                timerhandle = asyncio.get_running_loop().call_later(2,
                    shutdown_writer, nodeView, eventID)
                writerList[nodeView].timer = timerhandle
            else:
                del writerList[nodeView]
    else:
        logging.warning('Unrecognized tracking event {}'.format(ote))
    
async def main():
    log = start_logging()
    logsock = ctx.socket(zmq.SUB) # SUB socket for log publisher subscriptions
    logsock.subscribe(b'')
    dbLogMsgs = asyncio.Queue() # log data content messages for DBMS writer 
    try:
        await asyncio.gather(control_loop(logsock), 
                             process_logs(logsock, dbLogMsgs),
                             dbms_writer(dbLogMsgs)) 
    except (KeyboardInterrupt, SystemExit):
        log.warning('Ctrl-C was pressed or SIGTERM was received')
    except Exception as ex:  # traceback will appear in log
        log.exception('Unanticipated error with no Exception handler.')

def start_logging():
    log = logging.getLogger()
    handler = logging.handlers.RotatingFileHandler('camwatcher.log',
        maxBytes=15000, backupCount=5)
    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    return log

if __name__ == '__main__' :
    asyncio.run(main())
