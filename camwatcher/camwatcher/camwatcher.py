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
import threading
import traceback
import queue
import imagezmq
import zmq
import pandas as pd
from time import sleep
from datetime import datetime
from zmq.asyncio import Context as AsyncContext
from sentinelcam.camdata import CamData
from sentinelcam.utils import readConfig

CFG = readConfig(os.path.join(os.path.expanduser("~"), "camwatcher.yaml"))

outposts = {}                   # outpost image subscribers by (node,view)
threadLock = threading.Lock()   # coordinate updates to list of outpost subscribers
dbLogMsgs = queue.Queue()       # log data content messages for CSV writer 

# Helper class implementing an IO deamon thread as an image subscriber
class ImageSubscriber:

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
            raise TimeoutError(f"Timed out reading from publisher {self.publisher}")
        self._data_ready.clear()
        return self._data

    def _run(self):
        receiver = imagezmq.ImageHub(self.publisher, REQ_REP=False)
        while not self._stop:
            imagedata = receiver.recv_jpg()
            if imagedata[0].split('|')[0].split(' ')[1] == self.view:
                self._data = (datetime.now().isoformat(), imagedata[1])
                self._data_ready.set()
        receiver.close()

    def close(self):
        self._stop = True

# multiprocessing class implementing a subprocess image writer
class ImageStreamWriter:

    def __init__(self, node_view, publisher, imagedir):
        self.node_view = node_view
        self._writeImages = multiprocessing.Value('i', 0)
        self._eventQueue = multiprocessing.Queue()
        self.process = multiprocessing.Process(target=self._image_subscriber, args=(
            self._writeImages, self._eventQueue, publisher, node_view[1], imagedir))
        self.process.start()
        logging.debug(f"ImageStreamWriter started for {node_view} pid {self.process.pid}")
    
    def _set_datedir(self, dir, date):
        path = os.path.join(dir, date)
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
        return path

    def _image_subscriber(self, writeImages, eventQueue, publisher, view, outdir):
        while True:
            eventID = eventQueue.get()
            processEvent = True
            # start image subscription thread and begin frame capture loop
            try:
                receiver = ImageSubscriber(publisher, view)
                # always write at least one frame before closing
                dt, frame = receiver.receive()
                date_directory = self._set_datedir(outdir, dt[:10])
                while processEvent:
                    if len(dt) == 19: dt += ".000000"
                    jpegframe = "{}_{}_{}.jpg".format(
                        eventID, dt[:10], dt[11:].replace(':','.'))
                    jpegfile = os.path.join(date_directory, jpegframe)
                    with open(jpegfile,"wb") as f:
                        f.write(frame)
                    if writeImages.value:
                        dt, frame = receiver.receive()
                    else:
                        receiver.close()    # done, close and wait on another
                        processEvent = False
            except Exception as ex:
                print(f"ImageStreamWriter failure {self.node_view}")
                traceback.print_exc()  # see syslog for traceback

    def start(self, eventID):
        self._writeImages.value = 1
        self._eventQueue.put(eventID)

    def stop(self):
        self._writeImages.value = 0

# Disk I/O CSV writer thread
class CSVwriter:

    def __init__(self, dir, dataQ):
        self._openfiles = {}      # a list of open files by unique identifier
        self._folder = dir        # top-level folder for CSV files
        self._today = None        # cuurent date as 'YYYY-MM-DD'
        self._dataQ = dataQ       # queued data for CSV file
        self._stop = False
        self._thread = threading.Thread(target=self._run, args=())
        self._thread.daemon = True
        self._thread.start()
    
    def _set_index(self, node, view, evt, timestamp, camsize, type, is_new_event) -> str:
        _today = timestamp[:10] 
        logging.debug("CSVwriter index setup " + evt)
        date_directory = os.path.join(self._folder, _today)
        if _today != self._today:
            logging.debug("Date folder selection: " + _today)
            try:
                # if date value changes, insure folder exists 
                os.mkdir(date_directory) 
            except FileExistsError:
                pass
        self._today = _today
        if is_new_event:
            # write an entry into the date folder index
            try: 
                with open(os.path.join(date_directory, 'camwatcher.csv'), mode='at') as index:
                    index.write(','.join([node, view, timestamp, evt, str(camsize[0]), str(camsize[1]), type]) + "\n")
            except Exception as e:
                logging.error(f"CSVwriter failure updating index file for '{_today}': {str(e)}")
        return date_directory

    def _run(self):
        logging.debug(f"CSVwriter thread starting within {self._folder}")
        while not self._stop:
            if self._dataQ.empty():
                sleep(0.01)
                continue
            while not self._dataQ.empty():
                (_ref, _data) = self._dataQ.get()
                (_node, _view, _tag) = (_ref[0], _ref[1], _ref[3])
                _recType = None
                try:
                    _recType = _data['type']
                    if _recType == _tag:
                        self._openfiles[_ref].write(','.join([
                            _data['timestamp'],
                            str(_data['obj']),
                            str(_data['clas']),
                            str(_data['rect'][0]), 
                            str(_data['rect'][1]), 
                            str(_data['rect'][2]), 
                            str(_data['rect'][3])
                            ]) + "\n" )
                    elif _recType == 'start': 
                        f = open(os.path.join(self._set_index(
                            _node, _view, _data['id'], _data['timestamp'], _data['camsize'], _tag, _data['new']), 
                            _data['id'] + '_' + _tag + '.csv'), mode='wt')
                        f.write("timestamp,objid,classname,rect_x1,rect_y1,rect_x2,rect_y2\n") # write column headers
                        self._openfiles[_ref] = f # add to list
                    elif _recType == 'end':
                        logging.debug(f"CSVwriter closing file for {_ref}")
                        self._openfiles[_ref].close() # close file
                        del self._openfiles[_ref] # remove from list
                    else:
                        logging.warning(f"Tracking type {_recType} from {_ref} ignored by CSVwriter")
                except KeyError as keyval:
                    logging.error(f"CSVWriter dictionary lookup failure, record type ({_recType}), KeyError: {keyval}")
                except Exception as e:
                    logging.exception('CSVwriter thread unhandled exception')
                self._dataQ.task_done()
        logging.debug('CSVwriter closing')
        for f in self._openfiles.values():
            f.close()

    def close(self):
        self._stop = True
        self._thread.join()
  
# -------------------------------------------------------------------
# --------------     Sentinel Agent definition       ----------------
# -------------------------------------------------------------------
class SentinelTaskData:
    def __init__(self, jobid, task, node, date, event) -> None:
        self.jobID = jobid
        self.jobTask = task
        self.sourceNode = node
        self.eventDate = date
        self.eventID = event
        self.node = None
        self.view = None
        self.trkType = None
        self.csvOpened = False
        self.status = 'Started'
        self.elapsed = None
        self.framelist = []
        self._framestart = datetime.now()
        self._startidx = 0

    # TODO: Tasks running on the Sentinel can produce multiple result types,
    # and should be allowed to provide results from multiple events. Need support 
    # for multiple open CSV files in simultaneous use per job. New CSV files are 
    # opened as additional tracking types are introduced for the current event.
    # If the eventID changes, close all open CSV files for the current event. 

    def set_view(self, node, view) -> None:
        self.node = node
        self.view = view

    def get_taskref(self) -> tuple:
        return (self.node, self.view, self.jobID, self.trkType)
    
    def done(self, status, elapsed) -> None:
        self.status = status
        self.elapsed = elapsed

    def set_frame_start(self, framestart) -> None:
        if len(self.framelist) == 0:
            self._startidx = 0
        else:
            self._startidx = -1
            self._framestart = datetime.fromisoformat(framestart)
            for frametime in self.framelist:
                self._startidx += 1
                if frametime >= self._framestart: break
    
    def get_frame_start(self) -> datetime:
        return self._framestart
    
    def get_frame_byoffset(self, offset) -> datetime:
        frameidx = self._startidx + offset
        if frameidx < 0:
            frametime = self.framelist[0]
        elif frameidx > len(self.framelist) - 1:
            frametime = self.framelist[-1]
        else:
            frametime = self.framelist[frameidx]
        return frametime

class SentinelAgent:
    def __init__(self, config, data, logdir) -> None:
        self._cfg = config
        self._data = data
        self.process = multiprocessing.Process(target=self._agent_tasks, args=(
            self._cfg, self._data, logdir))
        self.process.start()
        logging.debug(f"Sentinel agent started, pid {self.process.pid}")
    
    def _agent_tasks(self, config, data, logdir):
        runningJobs = {}
        # subscribe to Sentinel result publication
        sentinel_log = zmq.Context.instance().socket(zmq.SUB)
        sentinel_log.subscribe(b'')
        sentinel_log.connect(config['publisher'])
        # start sentinel logger for the agent
        self._start_logger(logdir)
        # start CSV file writer
        csvQueue = queue.Queue()
        csv = CSVwriter(data['csvfiles'], csvQueue)
        cwData = CamData(data['csvfiles'], data['images'])
        # consume every logging record published from the sentinel
        while True:
            topic, msg = sentinel_log.recv_multipart()
            topics = topic.decode('utf8').strip().split('.')
            message = msg.decode('ascii')
            if topics[1] == 'INFO':
                try:
                    logdata = json.loads(message)
                    if 'flag' in logdata:
                        # This is a SUBMIT, START, or STOP
                        _flag = logdata['flag']
                        _jobid = logdata['jobid']
                        if _flag in ['SUBMIT', 'START'] and logdata['sink'] == config['datasink']:
                            # Event data belongs here, it is managed by this camwatcher instance.
                            _task = logdata['task']
                            _from = logdata['from']
                            _date = logdata['date']
                            _event = logdata['event']
                            logging.debug(f"{_flag} _task[{_task}] _from[{_from}] _date[{_date}] _event[{_event}] _job[{_jobid}]")
                            if _flag == 'SUBMIT':
                                pass             # Ignoring SUBMIT for now, does not seem to be needed for anything?
                            else:                # else job has a START, begin tracking this result set for storage.

                                runningJobs[_jobid] = SentinelTaskData(_jobid, _task, _from, _date, _event)
                        else:
                            if _jobid in runningJobs:
                                task_data = runningJobs[_jobid]
                                logging.debug(f"STOP _job[{_jobid}] status[{logdata['status']}] elapsed[{logdata['elapsed']}]")
                                runningJobs[_jobid].done(logdata['status'], logdata['elapsed'])
                                if task_data.csvOpened:
                                    # EOJ, close the CSV file
                                    _taskref = task_data.get_taskref()
                                    _csvData = {"type": "end"}
                                    _dataBlock = (_taskref, _csvData)
                                    csvQueue.put(_dataBlock)
                                del runningJobs[_jobid]

                            logging.info("EOJ ({}, {}), elapsed time: {} {}, Target sink ({}), source ({}).".format(
                                logdata['task'], logdata['status'], logdata['elapsed'], logdata['taskstats'], logdata['sink'], logdata['from']))

                    elif 'jobid' in logdata:
                        _jobid = logdata['jobid']
                        if _jobid not in runningJobs:
                            logging.info(message)
                        else:
                            task_data = runningJobs[_jobid]
                            _event = task_data.eventID
                            _refkey = logdata['refkey']
                            _ringctrl = logdata['ringctrl']
                            _trktype = logdata['trktype']
                            _framestart = logdata['start']
                            _frameoffset = logdata['offset']
                            _clas = logdata['clas']
                            _objid = logdata['objid']
                            _rect = logdata['rect']  # [int(msg[5]), int(msg[6]), int(msg[7]), int(msg[8])]
                            if task_data.trkType is None:
                                task_data.trkType = _refkey
                                # Was not yet assigned, must be a new result set just arriving from the sentinel 
                                cwData.set_date(_date)
                                cwData.set_event(_event)
                                _node = cwData.get_event_node()
                                _view = cwData.get_event_view()
                                task_data.set_view(_node, _view)
                                if _ringctrl == 'full':
                                    task_data.framelist = [datetime.strptime(_jpgfile[-30:-4],"%Y-%m-%d_%H.%M.%S.%f") 
                                        for _jpgfile in cwData.get_event_images()]
                                else:
                                    trkdata = cwData.get_event_data(_trktype)
                                    task_data.framelist = [pd.to_datetime(ts) for ts in trkdata['timestamp'].unique()]
                                logging.debug(f"Check event {_event} for '{_refkey}' tag, frames={len(task_data.framelist)}")
                                if len(task_data.framelist) > 0:
                                    if _refkey not in cwData.get_event_types():
                                        _newResult = True
                                    else:
                                        _newResult = False  # must be an update to an existing tracking set                                
                                    _csvData = {
                                        "view": _view,
                                        "id": _event,
                                        "timestamp": cwData.get_event_start().isoformat(),
                                        "type": "start",
                                        "new": _newResult,
                                        "camsize": (0, 0)  # TODO: retrive this from camwatcher event index
                                    }
                                    _taskref = task_data.get_taskref()
                                    _startBlock = (_taskref, _csvData)
                                    csvQueue.put(_startBlock)
                                    task_data.csvOpened = True
                                else:
                                    logging.warning(f"No images loaded, task {task_data.get_taskref()} ignored")
                                    del runningJobs[_jobid]
                                    continue
                            # map frame offset to the correct timetamp 
                            _desiredStart = datetime.fromisoformat(_framestart)
                            if _desiredStart != task_data.get_frame_start():
                                logging.debug(f"Sentinel agent adjust framestart={_framestart} for sentinel task {_taskref}")
                                task_data.set_frame_start(_framestart)
                            _frametime = task_data.get_frame_byoffset(_frameoffset)
                            # write result data to CSV fie 
                            _taskref = task_data.get_taskref()
                            _csvData = {
                                "timestamp": _frametime.isoformat(),
                                "type": _refkey,
                                "obj": _objid,
                                "clas": _clas,
                                "rect": _rect
                            }
                            _dataBlock = (_taskref, _csvData)
                            csvQueue.put(_dataBlock)
                    else:
                        logging.info(message)

                except (KeyError, ValueError):  # Yes, this is luxuriously lazy. Most of the high-volume logging content
                    logging.info(message)       # should be JSON. Some status tracking is intended to be human readable.
                except Exception:  
                    logging.exception(f"Exception parsing sentinel log '{message}'")
            else:
                if topics[1]   == 'ERROR'   : logging.error(message)
                elif topics[1] == 'WARNING' : logging.warning(message)
                elif topics[1] == 'CRITICAL': logging.critical(message)
                elif topics[1] == 'DEBUG'   : logging.debug(message)
                else:
                    logging.critical(f"Sentinel logging disruption: {topics}")

    def _start_logger(self, logdir):
        log = logging.getLogger()
        handler = logging.handlers.TimedRotatingFileHandler(
            os.path.join(logdir, 'sentinel.log'),
            when='midnight', backupCount=120)
        formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
        handler.setFormatter(formatter)
        log.addHandler(handler)
        # remove original handler inherited from parent fork()
        log.handlers.remove(log.handlers[0])
        log.setLevel(logging.INFO)
        return log
    # ----------------------------------------------------------------------
    # --------------    End of Sentinel Agent child process    -------------
    # ----------------------------------------------------------------------
    async def submit_post_event_task(self, node_view, event) -> None:
        request = {
            'task': self._cfg['post_event'],
            'node': node_view,
            'date': datetime.now().isoformat()[:10],
            'event': event,
            'sink': self._cfg['datasink'], 
            'pump': self._cfg['datapump'] 
        }
        msg = json.dumps(request)
        with AsyncContext.instance().socket(zmq.REQ) as sock:
            sock.connect(self._cfg['requests']) 
            await sock.send(msg.encode("ascii"))
    # ----------------------------------------------------------------------

async def dispatch_logger(topics, msg):
    if topics[1]   == 'ERROR'   : logging.error(f"[{topics[0]}] {msg}") 
    elif topics[1] == 'WARNING' : logging.warning(f"[{topics[0]}] {msg}") 
    elif topics[1] == 'CRITICAL': logging.critical(f"[{topics[0]}] {msg}") 
    elif topics[1] == 'INFO'    : logging.info(f"[{topics[0]}] {msg}") 
    elif topics[1] == 'DEBUG'   : logging.debug(f"[{topics[0]}] {msg}") 
    else:
        logging.critical(f"Outpost logging disruption [{'.'.join(topics)}] {msg}") 

async def dispatch_ote(node, ote_data, sentinel_agent):
    try:
        ote = json.loads(ote_data)
        eventID = ote["id"]
        view = ote['view']
        node_view = (node, view)
        ote2db = ((node, view, eventID, 'trk'), ote)
        if ote["type"] == 'trk':
            dbLogMsgs.put(ote2db)
        elif ote["type"] == 'start':
            dbLogMsgs.put(ote2db)
            if node_view in outposts:
                # Start image subscriber / JPEG file writer
                outposts[node_view].start(eventID)
            else:
                logging.error(f"ImageStreamWriter {node_view} not found")
        elif ote["type"] == 'end':
            dbLogMsgs.put(ote2db)
            if node_view in outposts:
                 # Stop image subscriber
                outposts[node_view].stop() 
                if sentinel_agent is not None:
                    # Submit post_event task request to sentinel
                    await sentinel_agent.submit_post_event_task(node_view, eventID)
        else:
            logging.warning(f'Unrecognized tracking type {ote["type"]}')
    except (ValueError, KeyError):
        logging.error(f"Failure parsing tracking event data: '{ote_data}'")

async def process_logs(loggers, sentinel_agent):
    while True:
        topic, msg = await loggers.recv_multipart()
        topics = topic.decode('utf8').strip().split('.')
        message = msg.decode('ascii')
        # trim any trailing newline from log message
        if message.endswith('\n'): message = message[:-1] 
        if topics[1] == 'INFO': # node name is in topics[0]
            category = message[:3] 
            if category == 'ote':   # object tracking event 
                await dispatch_ote(topics[0], message[3:], sentinel_agent)
                logging.debug(message)
            elif category == 'fps':  # Outpost image publishing heartbeat
                logging.info(f"Outpost health '{topics[0]}' {message[3:]}")
            elif category == 'Exi':  # this is the "Exit" message from an imagenode
                await dispatch_logger(topics, message)
            else:
                logging.warning("Unknown message category {} from {}".format(
                    category, ".".join(topics)))
        else:
            await dispatch_logger(topics, message)

async def control_loop(control_socket, log_socket):
    logging.info("CamWatcher control loop started.")
    while True:
        msg = await control_socket.recv()
        msg = msg.decode("ascii").split('|')
        result = 'OK'
        command = msg[0]
        # 'CameraUp' is only supported command, must be an outpost log publisher 
        # handoff as JSON-encoded dictionary in the second field. Can be used to
        # dynamically introduce a new outpost to a running camwatcher.
        #   {
        #     'node': 'outpost',
        #     'view':  'PiCamera',
        #     'logger': 'tcp://lab1:5565',
        #     'images': 'tcp://lab1:5567'
        #   }
        try:
            _outpost = json.loads(msg[1])
            _node = _outpost['node']
            with threadLock:
                _haveit = [n for (n, v) in outposts if n == _node]
                if len(_haveit) == 0:
                    _view = _outpost['view']
                    _new_outpost = (_node, _view)
                    _images = _outpost['images']
                    _logger = _outpost['logger']
                    outposts[_new_outpost] = ImageStreamWriter(_new_outpost, _images)
                    log_socket.connect(_logger)
                    logging.debug(f"New outpost registered {_new_outpost}.")
        except ValueError as e:
            result = 'Error'
            logging.error(f"JSON exception '{str(e)}' decoding camera handoff message: '{msg[1]}'")
        except KeyError as keyval:
            result = 'Error'
            logging.error(f"Invalid camera handoff, missing '{keyval}' in message: '{msg[1]}'")
        except Exception as e:  
            result = 'Error'
            logging.error(f"CamWatcher subscription failure {str(e)}")
        logging.debug(f"CamWatcher control port reply {result}")
        await control_socket.send(result.encode("ascii"))

async def main():
    _data = CFG['data']
    _logs = CFG['logs']
    csvdir = _data['csvfiles']
    imagedir = _data["images"]
    main_log = _logs['camwatcher']
    sentinel_log = _logs['sentinel']
    log = start_logging(main_log)
    csv = CSVwriter(csvdir, dbLogMsgs)
    agent = SentinelAgent(CFG['sentinel'], _data, sentinel_log) if 'sentinel' in CFG else None
    asyncCtx = AsyncContext.instance()
    asyncREP = asyncCtx.socket(zmq.REP)  # 0MQ async socket for control loop 
    asyncSUB = asyncCtx.socket(zmq.SUB)  # 0MQ async socket for camwatcher log subscriptions
    asyncREP.bind(f"tcp://*:{CFG['control_port']}")
    asyncSUB.subscribe(b'')
    with threadLock:
        _outpost_nodes = CFG["outpost_nodes"]
        for node in _outpost_nodes:
            _nodecfg = _outpost_nodes[node]
            node_view = (node, _nodecfg['view'])
            outposts[node_view] = ImageStreamWriter(node_view, _nodecfg['images'], imagedir)
            asyncSUB.connect(_nodecfg['logger'])
    try:
        await asyncio.gather(control_loop(asyncREP, asyncSUB), 
                             process_logs(asyncSUB, agent))
    except (KeyboardInterrupt, SystemExit):
        log.warning('Ctrl-C was pressed or SIGTERM was received')
    except Exception as ex:  # traceback will appear in log 
        log.exception('Unanticipated error with no Exception handler.')
    finally:
        asyncREP.close()
        asyncSUB.close()
        csv.close()
        log.info("camwatcher shutdown")

def start_logging(logdir):
    log = logging.getLogger()
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(logdir, 'camwatcher.log'),
        maxBytes=524288, backupCount=10)
    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log

if __name__ == '__main__' :
    asyncio.run(main())
