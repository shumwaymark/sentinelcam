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
import logging.config
import multiprocessing
import subprocess
import threading
import traceback
import queue
import zmq
import pandas as pd
from time import sleep
from datetime import date, datetime
from zmq.asyncio import Context as AsyncContext
from sentinelcam.camdata import CamData
from sentinelcam.utils import ImageSubscriber, readConfig

CFG = readConfig(os.path.join(os.path.expanduser("~"), "camwatcher.yaml"))

outposts = {}                        # outpost image subscribers by (node,view)
threadLock = threading.Lock()        # coordinate updates to list of outpost subscribers
dbLogMsgQ = queue.Queue()            # log data content messages for CSV writer 
dateIndxQ = multiprocessing.Queue()  # for creating new camwatcher index entries 

# multiprocessing class implementing a subprocess image writer
class ImageStreamWriter:

    def __init__(self, node_view, publisher, imagedir):
        self.node_view = node_view
        self._writeImages = multiprocessing.Value('i', 0)
        self._eventQueue = multiprocessing.Queue()
        self.process = multiprocessing.Process(target=self._image_subscriber, args=(
            self._writeImages, self._eventQueue, publisher, node_view[1], imagedir))
        self.process.start()
        logging.debug(f"ImageStreamWriter started for {node_view} pid {self.process.pid} in {imagedir}")
    
    def _set_datedir(self, dir, ymd):
        path = os.path.join(dir, ymd)
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
        return path

    def _image_subscriber(self, writeImages, eventQueue, publisher, view, outdir):
        receiver = ImageSubscriber(publisher, view)
        while True:
            eventID = eventQueue.get()
            processEvent = True
            # start image subscription thread and begin frame capture loop
            try:
                receiver.start()
                # always write at least one frame before closing
                dt, frame = receiver.receive()
                if (datetime.now() - datetime.fromisoformat(dt)).seconds > 1:
                    dt, frame = receiver.receive()  # TODO: Fix the need for this?
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
                        receiver.stop()    # done, stop and wait on another
                        processEvent = False
            except Exception as ex:
                print(f"ImageStreamWriter failure {self.node_view}")
                traceback.print_exc()  # see syslog for traceback
            finally:
                receiver.stop()
                self.stop()

    def start(self, eventID):
        logging.debug(f"start image subscriber {self.node_view} pid {self.process.pid}, event {eventID}")
        self._writeImages.value = 1
        self._eventQueue.put(eventID)

    def stop(self):
        logging.debug(f"stop image subscriber {self.node_view} pid {self.process.pid}")
        self._writeImages.value = 0

# Child subprocess for managing the camwatcher index. There will be one instance of this daemon subprocess
# per camwatcher. It provides a single point of control for all updates to the camwatcher event index files. 
# Rather than update the index directly, all CSV writers pass new index entries through a queue for update 
# here. The datapump also passes any event delete commands it receives into this same gauntlet via the 
# control socket. Allowing multiple processes to update the same filesystem object in an uncontrolled fashion
# is a direct path to chaos, destruction, and despair.
class CSVindex:

    CSV_new = 1
    CSV_delete = 2

    def __init__(self, indxQ):
        self.process = multiprocessing.Process(target=self._run, args=(indxQ,))
        self.process.start()
        logging.debug(f"CSVindex subprocess started, pid {self.process.pid}")
    
    def _run(self, indxQ):
        _csvdir = CFG['data']['csvfiles']
        _imgdir = CFG['data']['images']
        _delQ = queue.Queue()
        _thread = threading.Thread(target=self._purge_loop, args=(_delQ,))
        _thread.daemon = True
        _thread.start()
        while True:
            (cmd, msg) = indxQ.get()

            if cmd == CSVindex.CSV_new:
                # Write a new entry into the camwatcher event index
                (date_directory, node, view, evt, timestamp, camsize, type) = msg
                try: 
                    with open(os.path.join(date_directory, 'camwatcher.csv'), mode='at') as index:
                        index.write(','.join([node, view, timestamp, evt, str(camsize[0]), str(camsize[1]), type]) + "\n")
                except Exception as e:
                    logging.error(f"CSVindex failure updating index, event {evt}, [{date_directory}]: {str(e)}")

            elif cmd == CSVindex.CSV_delete:
                # This is an event delete command, remove all data. First, just the index entries. Then a 
                # background thread is tasked with cleaning up any image data and CSV tracking datasets.
                (_date, _event) = msg
                _sh = ["sed", "-i", f"/{_event}/d", os.path.join(_csvdir, _date, 'camwatcher.csv')]
                result = subprocess.run(_sh, shell=False, capture_output=True, text=True)
                if result.returncode != 0:
                    logging.error(f"CSVindex index delete error {result.returncode} for {_date}/{_event}")
                _delQ.put(f"rm {os.path.join(_csvdir, _date, ''.join([_event,'*']))}")
                _delQ.put(f"ls {os.path.join(_imgdir, _date, ''.join([_event,'*']))} | xargs rm")
   
    def _purge_loop(self, delQ):
        while True:
            cmd = delQ.get()
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                logging.warning(f"CSVindex data deletion failure {result.returncode} from '{cmd}'")
            delQ.task_done()
            sleep(2)
 
# Disk I/O CSV writer thread
class CSVwriter:
    def __init__(self, dir, dateIdx, dataQ):
        self._openfiles = {}      # a list of open files by unique identifier
        self._folder = dir        # top-level folder for CSV files
        self._today = None        # cuurent date as 'YYYY-MM-DD'
        self._dateIdx = dateIdx   # queue for CSV index updates
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
            try:
                # if date value changes, insure folder exists 
                os.mkdir(date_directory) 
            except FileExistsError:
                pass
        self._today = _today
        if is_new_event:
            # write an entry into the date folder index
            self._dateIdx.put((CSVindex.CSV_new, (date_directory, node, view, evt, timestamp, camsize, type)))
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
    def __init__(self, logdata) -> None:
        self.jobID = logdata['jobid']
        self.jobTask = logdata['task']
        self.sourceNode = logdata['from']
        self.eventDate = logdata['date']
        self.eventID = logdata['event']
        self.node = None
        self.view = None
        self.trkType = None
        self.csvOpened = False
        self.status = 'Started'
        self.elapsed = None
        self.framelist = []
        self._framestart = datetime.now()
        self._startidx = 0
        logging.debug(f"New job [{self.jobID}] from {self.sourceNode}, date={self.eventDate} event={self.eventID} task={self.jobTask}")

    # TODO: Tasks running on the Sentinel can produce multiple result types,
    # and should be allowed to provide results from multiple events. Need support 
    # for multiple open CSV files in simultaneous use per job. New CSV files are 
    # opened as additional tracking types are introduced for the current event.
    # If the eventID changes, close all open CSV files for the current event. 

    def set_view(self, node, view) -> None:
        self.node = node
        self.view = view

    def get_taskref(self) -> tuple:
        return (self.node, self.view, self.eventID, self.trkType)
    
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
    def __init__(self, config, dateIdxQ, name="Main") -> None:
        self._cfg = config
        self._name = name
        self.process = multiprocessing.Process(
            target=self._agent_tasks, 
            args=(config, 
                  CFG['data'], 
                  CFG['logconfigs']['sentinel_agent'], 
                  dateIdxQ))
        self.process.start()
        logging.debug(f"Sentinel agent started, pid {self.process.pid}")
    
    def _agent_tasks(self, config, data, logcfg, dateIdxQ):
        runningJobs = {}
        # subscribe to Sentinel result publication
        sentinel_log = zmq.Context.instance().socket(zmq.SUB)
        sentinel_log.subscribe(b'')
        sentinel_log.connect(config['publisher'])
        # configure internal logger
        if self._name != 'Main':
            logfile = logcfg['handlers']['file']['filename']
            logcfg['handlers']['file']['filename'] = f"{logfile[:-4]}_{self._name}.log"
        logging.config.dictConfig(logcfg)
        # start CSV file writer
        csvQueue = queue.Queue()
        _csv = CSVwriter(data['csvfiles'], dateIdxQ, csvQueue)
        cwData = CamData(data['csvfiles'], data['images'])
        # consume every logging record published from the sentinel
        while True:
            topic, msg = sentinel_log.recv_multipart()
            topics = topic.decode('utf8').strip().split('.')
            message = msg.decode('ascii')
            if topics[1] == 'INFO' and message[0] == '{':
                try:
                    logdata = json.loads(message)
                    if 'flag' in logdata:
                        # This is a SUBMIT, START, or EOJ
                        _flag = logdata['flag']
                        _jobid = logdata['jobid']
                        if _flag == 'START' and logdata['sink'] == config['datasink']:
                            # Event data belongs here, it is managed by this camwatcher instance.
                            runningJobs[_jobid] = SentinelTaskData(logdata)
                        elif _flag == 'EOJ':
                            if _jobid in runningJobs:
                                task_data = runningJobs[_jobid]
                                runningJobs[_jobid].done(logdata['status'], logdata['elapsed'])
                                if task_data.csvOpened:
                                    # EOJ, close the CSV file
                                    _csvData = {"type": "end"}
                                    _dataBlock = (task_data.get_taskref(), _csvData)
                                    csvQueue.put(_dataBlock)
                                del runningJobs[_jobid]

                            logging.info("EOJ ({}, {}), elapsed time: {} {}, event: {}, source: {}/{}".format(
                                logdata['task'], logdata['status'], logdata['elapsed'], logdata['taskstats'], 
                                logdata['event'], logdata['from'][0], logdata['from'][1]))
                            
                    elif 'jobid' in logdata:
                        _jobid = logdata['jobid']
                        if _jobid in runningJobs:
                            task_data = runningJobs[_jobid]
                            if task_data.trkType is None:
                                # Was not yet assigned, must be a new result set just arriving from the sentinel 
                                task_data.trkType = logdata['refkey']
                                cwData.set_date(task_data.eventDate)
                                cwData.set_event(task_data.eventID)
                                task_data.set_view(cwData.get_event_node(), cwData.get_event_view())
                                if logdata['ringctrl'] == 'full':
                                    task_data.framelist = [datetime.strptime(_jpgfile[-30:-4],"%Y-%m-%d_%H.%M.%S.%f") 
                                        for _jpgfile in cwData.get_event_images()]
                                else:
                                    trkdata = cwData.get_event_data(logdata['trktype'])
                                    task_data.framelist = [pd.to_datetime(ts) for ts in trkdata['timestamp'].unique()]
                                logging.debug(f"Check event {task_data.eventID} for '{task_data.trkType}' tag, frames={len(task_data.framelist)}")
                                if len(task_data.framelist) > 0:
                                    if task_data.trkType not in cwData.get_event_types():
                                        _newResult = True
                                    else:
                                        _newResult = False  # must be an update to an existing tracking set                                
                                    _csvData = {
                                        "view": cwData.get_event_view(),
                                        "id": task_data.eventID,
                                        "timestamp": cwData.get_event_start().isoformat(),
                                        "type": "start",
                                        "new": _newResult,
                                        "camsize": cwData.get_event_camsize()
                                    }
                                    _startBlock = (task_data.get_taskref(), _csvData)
                                    csvQueue.put(_startBlock)
                                    task_data.csvOpened = True
                                else:
                                    # TODO: More graceful handling, recovery, prevention needed here.
                                    # Most likely the result of a race condition between the sentinel 
                                    # and CSVwriter, where an image analysis task was started before 
                                    # the trk-specific CSV dataset was ready. 
                                    logging.error(f"No images loaded, task {task_data.get_taskref()} ignored")
                                    del runningJobs[_jobid]
                                    continue
                            # map frame start point and offset to the correct timetamp 
                            _framestart = logdata['start']
                            _frameoffset = logdata['offset']
                            if task_data.get_frame_start() != datetime.fromisoformat(_framestart):
                                logging.debug(f"Sentinel agent adjust framestart={_framestart} for sentinel task {task_data.get_taskref()}")
                                task_data.set_frame_start(_framestart)
                            _frametime = task_data.get_frame_byoffset(_frameoffset)
                            # write result data to CSV fie 
                            _csvData = {
                                "timestamp": _frametime.isoformat(),
                                "type": task_data.trkType,
                                "obj": logdata['objid'],
                                "clas": logdata['clas'],
                                "rect": logdata['rect']  # [int(msg[5]), int(msg[6]), int(msg[7]), int(msg[8])]
                            }
                            _dataBlock = (task_data.get_taskref(), _csvData)
                            csvQueue.put(_dataBlock)
                    else:
                        logging.debug(message)

                except ValueError as e:
                    logging.error(f"JSON exception {str(e)}, reading from log: {message}")
                except KeyError as keyval:
                    logging.error(f"Invalid logging record, '{keyval}' missing: {message}")
                except Exception:  
                    logging.exception(f"Exception parsing sentinel log: {message}")

            elif message[:4] == 'Pump':
                pass
            else:
                if message.endswith('\n')   : message = message[:-1]  # trim any trailing newline
                if topics[1]   == 'ERROR'   : logging.error(message)
                elif topics[1] == 'WARNING' : logging.warning(message)
                elif topics[1] == 'CRITICAL': logging.critical(message)
                elif topics[1] == 'DEBUG'   : logging.debug(message)
                elif topics[1] == 'INFO'    : logging.info(message)
                else:
                    logging.critical(f"Sentinel logging disruption: {topics}")

    # ----------------------------------------------------------------------
    # --------------    End of Sentinel Agent child process    -------------
    # ----------------------------------------------------------------------
    async def submit_post_event_tasks(self, node_view, event, tasklist) -> None:
        with AsyncContext.instance().socket(zmq.REQ) as sock:
            sock.connect(self._cfg['requests']) 
            for task in tasklist:
                request = {
                    'task': task[0],
                    'node': node_view,
                    'date': str(date.today()),
                    'event': event,
                    'sink': self._cfg['datasink'], 
                    'pump': self._cfg['datapump'],
                    'priority': task[1]
                }
                msg = json.dumps(request)
                await sock.send(msg.encode("ascii"))
                await sock.recv()
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
            dbLogMsgQ.put(ote2db)
        elif ote["type"] == 'start':
            dbLogMsgQ.put(ote2db)
            if node_view in outposts:
                # Start image subscriber / JPEG file writer
                outposts[node_view].start(eventID)
            else:
                logging.error(f"ImageStreamWriter {node_view} not found")
        elif ote["type"] == 'end':
            dbLogMsgQ.put(ote2db)
            if node_view in outposts:
                 # Stop image subscriber
                outposts[node_view].stop() 
                # Submit post_event task request(s) to sentinel
                tasklist = ote['tasks']
                logging.debug(f"post event {eventID} tasklist: {tasklist}")
                if len(tasklist) > 0:
                    await sentinel_agent.submit_post_event_tasks(node_view, eventID, tasklist)
                else:
                    dateIndxQ.put((CSVindex.CSV_delete, (str(date.today()), eventID)))
        else:
            logging.warning(f'Unrecognized tracking type {ote["type"]}')
    except (ValueError, KeyError):
        logging.error(f"Failure parsing tracking event data: '{ote_data}'")

async def process_logs(loggers, sentinel_agent):
    logging.info("camwatcher log subscriber started")
    while True:
        topic, msg = await loggers.recv_multipart()
        topics = topic.decode('utf8').strip().split('.')
        message = msg.decode('ascii')
        if message.endswith('\n'): message = message[:-1]  # trim any trailing newline
        if len(topics) < 2:
            logging.error(f"Malformed logging topic {topics}, {message}")
        elif topics[1] == 'INFO': # node name is in topics[0]
            category = message[:3] 
            if category == 'ote':   # object tracking event 
                await dispatch_ote(topics[0], message[3:], sentinel_agent)
                logging.debug(message)
            elif category == 'fps':  # Outpost image publishing heartbeat
                logging.info(f"Outpost health '{topics[0]}' {message[3:]}")
            else:  # pass everything else along to the logger
                await dispatch_logger(topics, message)
        else:
            await dispatch_logger(topics, message)

async def control_loop(control_socket, log_socket):
    logging.info("camwatcher control loop started")
    _agents = {}  # list of dynamically requested ad hoc agents 
    while True:
        result = 'OK'
        msg = await control_socket.recv()
        payload = msg.decode("ascii")
        try:
            request = json.loads(payload)
            if 'cmd' in request:
                if request['cmd'] == 'CamUp':
                    # Can be used to dynamically introduce a new outpost to a running camwatcher.
                    _node = request['node']
                    with threadLock:
                        _haveit = [n for (n, v) in outposts if n == _node]
                        if len(_haveit) == 0:
                            _view = request['view']
                            _new_outpost = (_node, _view)
                            outposts[_new_outpost] = ImageStreamWriter(_new_outpost, request['images'], CFG['data']['images'])
                            log_socket.connect(request['logger'])
                            logging.info(f"New outpost registered {_new_outpost}.")
                elif request['cmd'] == 'Agent':
                    # Used to dynamically spawn ad hoc Sentinel Agents within a running camwatcher. 
                    name = request['name']
                    if name not in _agents:
                        new_agent = {}
                        new_agent['name'] = name
                        new_agent['requests'] = request['requests'] 
                        new_agent['publisher'] = request['publisher']
                        new_agent['datapump'] = request['datapump']
                        new_agent['datasink'] = request['datasink']
                        _agents[name] = SentinelAgent(new_agent, dateIndxQ, name)
                elif request['cmd'] == 'DelEvt':
                    # TODO: This code not currently restricted by FaceList.event_locked() control
                    dateIndxQ.put((CSVindex.CSV_delete, (request['date'], request['event'])))
                else:
                    result = 'Error'
                    logging.error(f"Unknown control command: {request['cmd']}")
            else:
                result = 'Error'
                logging.warning("No control command was specified, request ignored")
        except ValueError as e:
            result = 'Error'
            logging.error(f"JSON exception '{str(e)}' decoding camera handoff message: '{msg}'")
        except KeyError as keyval:
            result = 'Error'
            logging.error(f"Invalid control message, missing '{keyval}' in message: '{msg}'")
        except Exception as e:  
            result = 'Error'
            logging.error(f"CamWatcher control message failure, '{msg}': {str(e)}")
        logging.debug(f"CamWatcher control port reply {result}")
        await control_socket.send(result.encode("ascii"))

async def main():
    _data = CFG['data']
    logging.config.dictConfig(CFG['logconfigs']['camwatcher_internal'])
    log = logging.getLogger()
    _csvidx = CSVindex(dateIndxQ)
    csv = CSVwriter(_data['csvfiles'], dateIndxQ, dbLogMsgQ)
    agent = SentinelAgent(CFG['sentinel'], dateIndxQ)
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
            outposts[node_view] = ImageStreamWriter(node_view, _nodecfg['images'], _data["images"])
            asyncSUB.connect(_nodecfg['logger'])
    try:
        await asyncio.gather(control_loop(asyncREP, asyncSUB), 
                             process_logs(asyncSUB, agent))
    except (KeyboardInterrupt, SystemExit):
        log.warning('Ctrl-C was pressed or SIGTERM was received')
    except Exception:  # traceback will appear in log 
        log.exception('Unanticipated error with no Exception handler.')
    finally:
        asyncREP.close()
        asyncSUB.close()
        csv.close()
        log.info("camwatcher shutdown")

if __name__ == '__main__' :
    asyncio.run(main())
