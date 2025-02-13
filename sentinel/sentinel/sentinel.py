"""sentinel: The heart of the SentinelCam distributed vision engine. 

Copyright (c) 2023 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import asyncio
import json
import logging
import logging.config
import cv2
import numpy as np
import pandas as pd
from ast import literal_eval
from datetime import datetime
import multiprocessing
from multiprocessing import sharedctypes
import os
import time
import threading
import traceback
import queue
import uuid
import zmq
from zmq.asyncio import Context as AsyncContext
from sentinelcam.datafeed import DataFeed
from sentinelcam.taskfactory import TaskFactory
from sentinelcam.utils import readConfig
import msgpack
#import simplejpeg

CFG = readConfig(os.path.join(os.path.expanduser("~"), "sentinel.yaml"))
SOCKDIR = CFG["socket_dir"]

ctxAsync = AsyncContext.instance()
ctxBlocking = zmq.Context.shadow(ctxAsync.underlying)
jobLock = threading.Lock()
taskFeed = queue.Queue()

taskList = {}  # All JobRequest objects by JobID
jobList = {}   # Those task requests which should currently be running
 
class JobRequest:
    
    Status_UNDEFINED = 0
    Status_QUEUED = 1
    Status_RUNNING = 2 
    Status_DONE = 3
    Status_FAILED = 4
    Status_CHAINED = 5
    Status_CANCELED = 6

    Status = ["Undefined", "Queued", "Running", "Done", "Failed", "Chained", "Canceled"]

    def __init__(self, sink, node, date, event, pump, taskname, priority=2) -> None:
        self.jobID = uuid.uuid1().hex
        self.jobTask = taskname
        self.jobClass = None
        self.jobStatus = JobRequest.Status_QUEUED
        self.jobSubmitTime = datetime.now()
        self.jobStartTime = None
        self.jobEndTime = None
        self.sourceNode = node
        self.dataSink = sink
        self.eventDate = date
        self.eventID = event
        self.datapump = pump  # datapump connection string
        self.camsize = (0,0)  # required per event; learn up front, dynamically
        self.engine = None
        self.priority = priority
        self.image_cnt = 0
        self.image_rate = 0.0
        logging.info(str(self.start_Message('SUBMIT')))
        with jobLock:
            taskList[self.jobID] = self

    def registerJOB(self, engine) -> None:
        self.jobStartTime = datetime.now()
        self.jobStatus = JobRequest.Status_RUNNING
        self.engine = engine
        logging.info(str(self.start_Message('START')))
        with jobLock:
            jobList[self.jobID] = self

    def deregisterJOB(self, status, stats) -> None:
        self.jobEndTime = datetime.now()
        self.jobStatus = status
        self.image_cnt = stats[0]
        self.image_rate = stats[1]
        logging.info(str(self.stop_Message()))
        with jobLock:
            if self.jobID in jobList:
                logging.debug(f"strike jobList[{self.jobID}], status now {JobRequest.Status[status]}")
                del jobList[self.jobID]

    def _timeVals(self) -> tuple:
        # Returns tuple with 3 formatted strings, or None when factor missing
        start_time, end_time, elapsed_time = None, None, None
        if self.jobStartTime is not None:
            start_time = self.jobStartTime.isoformat()
            if self.jobEndTime is not None:
                end_time = self.jobEndTime.isoformat()
                elapsed_time = str(self.jobEndTime - self.jobStartTime)
        return (start_time, end_time, elapsed_time)  

    def start_Message(self, stage) -> str:
        return json.dumps({
            'flag': stage,
            'jobid': self.jobID,
            'task': self.jobTask,
            'from': self.sourceNode,
            'sink': self.dataSink,
            'date': self.eventDate,
            'event': self.eventID
        })

    def stop_Message(self) -> str:
        (start_time, end_time, elapsed_time) = self._timeVals()
        return json.dumps({
            'flag': 'EOJ',
            'jobid': self.jobID,
            'task': self.jobTask,
            'from': self.sourceNode,
            'sink': self.dataSink,
            'pump': self.datapump,
            'date': self.eventDate,
            'event': self.eventID,
            'status': JobRequest.Status[self.jobStatus],
            'elapsed': elapsed_time,
            'taskstats': [self.image_cnt, self.image_rate]
        })

    def summary_JSON(self) -> str:
        (start_time, end_time, elapsed_time) = self._timeVals()
        return json.dumps({
            'flag': 'JOB',
            'node': self.sourceNode,
            'date': self.eventDate,
            'task': self.jobTask,
            'class': self.jobClass,
            'sink': self.dataSink,
            'status': JobRequest.Status[self.jobStatus],
            'submited': self.jobSubmitTime.isoformat(),
            'started': start_time,
            'ended': end_time,
            'elapsed': elapsed_time,
            'priority': self.priority,
            'engine': self.engine,
            'event': self.eventID,
            'jobid': self.jobID,
            'images': self.image_cnt,
            'rate': self.image_rate
        })
    
    def full_history_report() -> None:
        with jobLock:
            logging.info("Start of history.")
            for jobreq in taskList.values():
                logging.info(jobreq.summary_JSON())
        logging.info("End of history.")

class RingWire:
    def __init__(self, socketDir, engineName) -> None:
        self._wire = ctxBlocking.socket(zmq.REP)
        self._wire.bind(f"ipc://{socketDir}/{engineName}")
        self._poller = zmq.Poller()
        self._poller.register(self._wire, zmq.POLLIN)

    def ready(self) -> bool:
        events = dict(self._poller.poll(0))
        if self._wire in events:
            return events[self._wire] == zmq.POLLIN
        else:
            return False    
    
    def recv(self) -> tuple:
        return msgpack.unpackb(self._wire.recv(), use_list=False)

    def send(self, result) -> None:
        self._wire.send(msgpack.packb(result))

    def __del__(self) -> None:
        self._wire.close()

class RingBuffer:
    def __init__(self, wh, length) -> None:
        dtype = np.dtype('uint8')
        shape = (wh[1], wh[0], 3)
        self._length = length
        self._buffers = [sharedctypes.RawArray('c', shape[0]*shape[1]*shape[2]) for i in range(length)]
        self._frames = [np.frombuffer(buffer, dtype=dtype).reshape(shape) for buffer in self._buffers]
        self.reset()
    
    def reset(self) -> None:
        self._count = 0
        self._start = 0
        self._end = 0

    def bufferList(self) -> list:
        return self._buffers

    def frameList(self) -> list:
        return self._frames

    def isEmpty(self) -> bool:
        return self._count == 0

    def isFull(self) -> bool:
        return self._count == self._length

    def put(self, frame) -> None:
        self._frames[self._end][:] = frame[:]  # np.copyto(self._sharedFrame, frame)
        self._count += 1
        self._end += 1
        self._end %= self._length

    def get(self) -> int:
        # Retrieve current start position for sending to child process
        if self.isEmpty():
            return -1
        else:
            return self._start

    def frame_complete(self) -> None:
        # Advance the start pointer only when the child process is done with it. 
        # This avoids a race condition between the parent and child. After initial 
        # read from buffer, invoking this just prior to subsequent get() operations 
        # prevents overlaying the current frame in use. 
        self._count -= 1
        self._start += 1
        self._start %= self._length

class JobTasking:
    """ Implements a TaskEngine for the JobManager. Encapsulates a forked child 
    subprocess to execute job logic on a task engine.
    
    Parameters
    ----------
    engineName : str
        Identifying name for this engine.

    pump : str
        Connection string for the default datapump. Each task engine will need 
        a DataFeed. Establishes the 0MQ context for other control sockets.

    taskCFG : dict
        This is the task list. A configuration dictionary of available tasks.

    accelerator : str
        The co-processor configured for this task engine.

    taskQ : multiprocessing.Queue 
        Used for sending job requests to the task engine.

    rawRingbuff : dict
        The image frame ring buffers for this task engine keyed by image size. 
        The items are a list of shared memory blocks. These are references to
        a multiprocessing.sharedctypes.RawArray. Each will be redefined as an 
        appropriate NumPy array by the child process. 
    """

    def __init__(self, engineName, pump, taskCFG, accelerator, taskQ, rawRingbuff) -> None:
        self._engine = engineName
        self._taskQ = taskQ
        self._rawRingBuffer = rawRingbuff
        self.process = multiprocessing.Process(target=self.taskHost, args=(
            engineName, pump, taskCFG, accelerator, taskQ, rawRingbuff))
        self.process.start()

    def terminate(self) -> None:
        if self.process.is_alive():
            self.process.kill()
            self.process.join()

    # --------------------------------------------------------------------------------------------------
    def taskHost(self, engineName, pump, taskCFG, accelerator, taskQ, _ringbuff):
    # --------------------------------------------------------------------------------------------------
        try:
            taskpump = pump
            feed = DataFeed(taskpump)                        # useful for task-specific datapump access
            ringWire = feed.zmq_context.socket(zmq.REQ)      # IPC signaling for ring buffer control
            publisher = feed.zmq_context.socket(zmq.PUB)     # job result publication
            ringWire.connect(f"ipc://{SOCKDIR}/{engineName}")
            publisher.bind(f"ipc://{SOCKDIR}/{engineName}.PUB")
            ringWire.send(msgpack.packb(0))  # send the ready handshake
            ringbuffers = {}
            for wh in _ringbuff:
                dtype = np.dtype('uint8')
                shape = (wh[1], wh[0], 3)
                ringbuffers[wh] = [np.frombuffer(buffer, dtype=dtype).reshape(shape) for buffer in _ringbuff[wh]]
            handshake = ringWire.recv()  # wait for subscriber to connect

            # Hang tight on the scoping here, best to keep these variables close at hand for the four
            # local function definitions below. Also enhances eyes-on clarity for scrutiny during code review.
            self.jobreq = None
            self.frame_start = None
            self.frame_offset = 0
            self.imagesize = (0,0)
            self.ringbuff = []
            self.ringctrl = 'full'
            self.trktype = 'trk'

            def ringStart(frametime, newEvent=None) -> int:
                self.frame_start = frametime.isoformat()
                self.frame_offset = 0
                _start_command = (JobManager.ReadSTART, (self.frame_start, newEvent, self.ringctrl, self.trktype))
                ringWire.send(msgpack.packb(_start_command))
                if newEvent:
                    # wait here for confirmation of ring buffer assignment
                    self.jobreq = taskQ.get()  
                    if self.jobreq.camsize != self.imagesize and self.jobreq.camsize != (0,0):
                        self.imagesize = self.jobreq.camsize
                        self.ringbuff = ringbuffers[self.imagesize]
                bucket = msgpack.unpackb(ringWire.recv())
                return bucket

            def ringNext() -> int:
                self.frame_offset += 1
                ringWire.send(msgpack.packb((JobManager.ReadNEXT, None)))
                bucket = msgpack.unpackb(ringWire.recv())
                return bucket
            
            def getRing() -> list:
                return self.ringbuff

            def publish(msg, frameref=None, cwUpd=False) -> None:
                if frameref is not None:
                    frame = (self.jobreq.jobID, frameref, self.ringctrl, self.frame_start, self.frame_offset)
                    msg = frame + msg
                    if cwUpd:
                        cwUpdate = {
                            "jobid": msg[0],
                            "refkey": msg[1],
                            "ringctrl": msg[2],
                            "start": msg[3],
                            "offset": msg[4],
                            "clas": msg[5],
                            "objid": msg[6],
                            'rect': [int(msg[7]), int(msg[8]), int(msg[9]), int(msg[10])],
                            'trktype': self.trktype
                        }
                        msg = json.dumps(cwUpdate)
                envelope = (TaskEngine.TaskSTATUS, msg)
                publisher.send(msgpack.packb(envelope))

            failCnt = 0
            while failCnt < TaskEngine.FAIL_LIMIT:
                if taskQ.empty():
                    time.sleep(0.05)
                else:
                    self.jobreq = taskQ.get()
                    eoj_status = TaskEngine.TaskDONE  # assume success
                    nextTask = None
                    try:
                        if self.jobreq.datapump != taskpump:
                            taskpump = self.jobreq.datapump
                            feed.zmq_socket.connect(taskpump)
                        if self.jobreq.eventID and self.jobreq.camsize != self.imagesize:
                            self.imagesize = self.jobreq.camsize
                            self.ringbuff = ringbuffers[self.imagesize]

                        # ----------------------------------------------------------------------
                        #   Task Initialization
                        # ----------------------------------------------------------------------
                        taskcfg = taskCFG[self.jobreq.jobTask]
                        if not self.jobreq.eventID:
                            trackingData = None
                        else:
                            # preload desired tracking set and retrieve starting image
                            self.trktype = taskcfg['trk_type'] if 'trk_type' in taskcfg else 'trk'
                            self.ringctrl = taskcfg['ringctrl'] if 'ringctrl' in taskcfg else 'full'
                            trackingData = feed.get_tracking_data(self.jobreq.eventDate, self.jobreq.eventID, self.trktype)
                            if self.ringctrl == 'trk':
                                startframe = trackingData.iloc[0]['timestamp'] 
                            else:
                                startframe = feed.get_image_list(self.jobreq.eventDate, self.jobreq.eventID)[0]

                        if 'alias' in taskcfg:
                            self.jobreq.jobTask = taskcfg['alias']
                        if 'chain' in taskcfg:
                            nextTask = taskcfg['chain']

                        task = TaskFactory(self.jobreq, trackingData, feed, taskcfg["config"], accelerator)
                        # Hang hooks for task references to ring buffer and publisher
                        task.ringStart = ringStart
                        task.ringNext = ringNext
                        task.getRing = getRing
                        task.publish = publish
                        startMsg = (TaskEngine.TaskSTARTED, self.jobreq.jobID)
                        publisher.send(msgpack.packb(startMsg))

                        # ----------------------------------------------------------------------
                        #   Execute task
                        # ----------------------------------------------------------------------
                        if not self.jobreq.eventID:
                            # ------------------------------------------------------------------------
                            #   No starting event? No pipeline() loop supported. Have no tracking data, 
                            #   and no starting frame. Will call the pipeline() once for this task.
                            # ------------------------------------------------------------------------
                            task.pipeline(None)

                        else:
                            # ------------------------------------------------------------------------
                            #   Start the ring buffer
                            # ------------------------------------------------------------------------
                            bucket = ringStart(startframe)

                            # ------------------------------------------------------------------------
                            #   Frame loop for an image pipeline task
                            # ------------------------------------------------------------------------
                            while bucket != JobManager.ReadEOF:
                                if task.pipeline(self.ringbuff[bucket]):
                                    bucket = ringNext()
                                else:
                                    bucket = JobManager.ReadEOF

                        # ----------------------------------------------------------------------
                        #   Publish final results 
                        # ----------------------------------------------------------------------
                        task.finalize()

                        if nextTask and eoj_status == TaskEngine.TaskDONE:
                            msg = (TaskEngine.TaskCHAIN, (self.jobreq.jobID, nextTask))
                            publisher.send(msgpack.packb(msg))

                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except DataFeed.TrackingSetEmpty as e:
                        msg = (TaskEngine.TaskERROR, f"No tracking data for ({e.date}, {e.evt}, {e.trk})")
                        publisher.send(msgpack.packb(msg))
                        eoj_status = TaskEngine.TaskFAIL
                    except DataFeed.ImageSetEmpty as e:
                        msg = (TaskEngine.TaskERROR, f"No images for ({e.date}, {e.evt})")
                        publisher.send(msgpack.packb(msg))
                        eoj_status = TaskEngine.TaskFAIL
                    except KeyError as keyval:
                        msg = (TaskEngine.TaskERROR, f"taskHost() internal key error '{keyval}'")
                        publisher.send(msgpack.packb(msg))
                        eoj_status = TaskEngine.TaskFAIL
                    except cv2.error as e:
                        msg = (TaskEngine.TaskERROR, f"OpenCV error, {str(e)}")
                        publisher.send(msgpack.packb(msg))
                        eoj_status = TaskEngine.TaskFAIL
                        failCnt += 1
                    #except TimeoutError as e:
                    #    msg (TaskEngine.TaskERROR, str(e))
                    #    publisher.send(msgpack.packb(msg))
                    #    eoj_status = TaskEngine.TaskFAIL
                    except Exception as e:
                        traceback.print_exc()  # see syslog for traceback  
                        msg = (TaskEngine.TaskERROR, f"taskHost({self.jobreq.eventDate}, {self.jobreq.eventID}), {str(e)}")
                        publisher.send(msgpack.packb(msg))
                        eoj_status = TaskEngine.TaskFAIL
                        failCnt += 1
                    else:
                        failCnt = 0
                    finally:
                        publisher.send(msgpack.packb((eoj_status, self.jobreq.jobID)))
            
            # Limit on successive failures exceeded
            msg = (TaskEngine.TaskBOMB, f"{engineName}: JobTasking failure limit exceeded.")
            publisher.send(msgpack.packb(msg))

        except (KeyboardInterrupt, SystemExit):
            print(f"JobTasking shutdown {engineName}.")
        except Exception as e:
            msg = (TaskEngine.TaskBOMB, f"{engineName}: JobTasking failure, {str(e)}")
            publisher.send(msgpack.packb(msg))
            traceback.print_exc()  # see syslog for traceback
        finally:
            feed.close()
            ringWire.close()
            publisher.close()
        # ----------------------------------------------------------------------
        #                         End of TaskEngine
        # ----------------------------------------------------------------------

class TaskEngine:

    FAIL_LIMIT = 3

    TaskSTATUS = 0
    TaskSUBMIT = 1
    TaskSTARTED = 2
    TaskDONE = 3
    TaskFAIL = 4
    TaskCHAIN = 5
    TaskCANCELED = 6
    TaskWARNING = 7
    TaskERROR = 8
    TaskBOMB = -1

    def __init__(self, engineName, config, ringCFG, taskCFG, pump, asyncSUB) -> None:
        self.name = engineName
        self.job_classes = config["classes"]
        self.accelerator = config["accelerator"]
        self.taskCFG = taskCFG
        self.taskQ = multiprocessing.Queue()
        self.wire = RingWire(SOCKDIR, engineName)
        ringmodel = ringCFG[config["ring_buffers"]]
        ringsetups = [literal_eval(ring) for ring in ringmodel.values()]
        self.ringbuffers = {wh: RingBuffer(wh, l) for (wh, l) in ringsetups}
        self.rawBuffers = {wh: self.ringbuffers[wh].bufferList() for wh in self.ringbuffers}
        self.jobreq = None
        self.cursor = None
        self.imagesize = (0,0)  # current image size 
        self.ringBuffer = None  # current RingBuffer 
        self.dataFeed = None    # current DataFeed
        # Ready to fork() the child subprocess for this task engine:
        self._engine = JobTasking(engineName, pump, taskCFG, self.accelerator, self.taskQ, self.rawBuffers)
        # establish handshake with child, connect to result publisher before continuing
        handshake = self.wire.recv()
        asyncSUB.connect(f"ipc://{SOCKDIR}/{engineName}.PUB")
        self.wire.send(handshake)

    def getName(self) -> str:
        return self.name
    
    def getClasses(self) -> list:
        return self.job_classes

    def getJobID(self) -> str:
        return self.jobreq.jobID if self.jobreq else None
        
    def getJobRequest(self) -> JobRequest:
        return self.jobreq
 
    def newEvent(self, date, evt, wh) -> None:
        self.jobreq.eventDate = date
        self.jobreq.eventID = evt
        self.jobreq.camsize = wh

    def start_job(self, jobreq) -> bool:
        confirm_start = True
        if jobreq.eventID and self.imagesize != jobreq.camsize:
            if jobreq.camsize in self.ringbuffers:
                self.imagesize = jobreq.camsize
                self.ringBuffer = self.ringbuffers[self.imagesize]
            else:
                logging.error("engine[{}]: RingBuffer definition {} not supported ({},{},{})".format(
                    jobreq.engine, jobreq.camsize, jobreq.dataSink, jobreq.eventDate, jobreq.eventID)
                )
                confirm_start = False
        if confirm_start:
            logging.debug(f"{jobreq.engine}: starting job {jobreq.jobID}")
            self.jobreq = jobreq
            self.taskQ.put(jobreq)
            self.task_start = time.time()
            self.image_cnt = 0
        return confirm_start

    def have_request(self) -> bool:
        return self.wire.ready()

    def get_request(self) -> tuple:
        return self.wire.recv()

    def send_response(self, resp) -> None:
        self.image_cnt += 1
        self.wire.send(resp)

    def get_image_cnt(self) -> int:
        return self.image_cnt 

    def get_image_rate(self) -> float:
        return round((self.get_image_cnt() / (time.time() - self.task_start)), 2)

    def is_alive(self) -> bool:
        return self._engine.process.is_alive()

    def cancel(self) -> None:
        # TODO: kill the child process here 
        #self.taskFlag.value = TaskEngine.TaskCANCELED
        pass

class JobManager:

    JobSTATUS = 0
    JobSUBMIT = 1
    JobSTART = 2
    JobCANCEL = 3

    ReadSTART = 10
    ReadNEXT = 11
    ReadEOF = -1
    ReadNOP = 0

    def __init__(self, engineCFG, ringCFG, taskCFG, default_pump, _asyncSUB) -> None:
        self.ondeck = {}
        self.engines = {}
        self.datafeeds = {}
        for engine in engineCFG:
            self.engines[engine] = TaskEngine(engine, engineCFG[engine], ringCFG, taskCFG, default_pump, _asyncSUB)
            for jobclass in self.engines[engine].getClasses():
                self.ondeck[jobclass] = None
        self._setPump(default_pump)
        self.taskmenu = taskCFG
        self._stop = False
        self._thread = threading.Thread(target=self._jobThread, args=())
        self._thread.daemon = True
        self._thread.start()

    def _setPump(self, pump) -> DataFeed:
        if not pump in self.datafeeds:
            self.datafeeds[pump] = DataFeed(pump)
        return self.datafeeds[pump]

    def _releaseJob(self, jobid, engine) -> None:
        logging.debug(f"Release job {jobid}")
        jreq = taskList[jobid]
        jreq.registerJOB(engine)
        self.engines[engine].dataFeed = self._setPump(jreq.datapump)
        if jreq.eventID:
            jreq.camsize = self._getFrameDimensons(jreq)
        if not self.engines[engine].start_job(jreq):
            jreq.deregisterJOB(TaskEngine.TaskFAIL, (0,0))
            self.ondeck[jreq.jobClass] = None

    def _chainJob(self, jobid, task):
        logging.debug(f"Job {jobid} requested chain to {task}")
        jreq = taskList[jobid]
        engine = self.engines[jreq.engine]
        chained = JobRequest(jreq.dataSink, 
                             jreq.sourceNode, 
                             jreq.eventDate, 
                             jreq.eventID, 
                             jreq.datapump, 
                             task,
                             jreq.priority)
        chained.camsize = jreq.camsize
        chained.jobClass = self.taskmenu[task]['class']
        if chained.jobClass in engine.getClasses():
            task_stats = (engine.get_image_cnt(), engine.get_image_rate())
            jreq.deregisterJOB(TaskEngine.TaskDONE, task_stats)
            chained.registerJOB(jreq.engine)
            if not engine.start_job(chained):
                chained.deregisterJOB(TaskEngine.TaskFAIL, (0,0))

    def _getFrameDimensons(self, jreq) -> tuple:
        datafeed = self._setPump(jreq.datapump)
        cwIndx = datafeed.get_date_index(jreq.eventDate)
        trkevt = cwIndx.loc[(cwIndx['event'] == jreq.eventID) & (cwIndx['type'] == 'trk')]
        if len(trkevt.index) > 0:
            _camsize = (int(trkevt.iloc[0].width), int(trkevt.iloc[0].height))
        else:
            _camsize = (0,0)
            logging.error(f"_getFrameDimensions() failed for event {(jreq.eventDate,jreq.eventID)}")
        logging.debug(f"Learned image dimensions: {_camsize}")
        return _camsize

    def _feedStart(self, taskEngine, key) -> None:
        jreq = taskEngine.getJobRequest()
        (startframe, _newEvent, _ringctrl, _trktype) = key
        if startframe:
            _valid = True
        if _newEvent:  
            # When changing events, potentially assign a different ring buffer
            jreq.eventDate = _newEvent[0]
            jreq.eventID = _newEvent[1]
            logging.debug(f"_feedStart() {taskEngine.getName()}, {startframe}, {jreq.eventDate}, {jreq.eventID}")
            _camsize = self._getFrameDimensons(jreq)
            if _camsize != jreq.camsize:
                if _camsize in taskEngine.ringbuffers:
                    taskEngine.ringBuffer = taskEngine.ringbuffers[_camsize]
                else:
                    logging.error(f"_feedStart() failed. RingBuffer {_camsize} not supported by {taskEngine.getName()}.")
                    _camsize = jreq.camsize
                    _valid = False
            taskEngine.newEvent(jreq.eventDate, jreq.eventID, _camsize)
            taskEngine.taskQ.put(taskEngine.getJobRequest())  # confirm event change readiness with task engine 
        if not _valid:
            taskEngine.ringBuffer.reset()
            taskEngine.cursor = None
        else:
            framestart = datetime.fromisoformat(startframe)
            if _ringctrl == 'full':
                frametimes = taskEngine.dataFeed.get_image_list(jreq.eventDate, jreq.eventID)
            else:
                evtData = taskEngine.dataFeed.get_tracking_data(jreq.eventDate, jreq.eventID, _trktype)
                # When multiple tracking records are present for the same frame, image data should only be read
                # once. It is task responsibility to internally align tracking data with each image provided.
                frametimes = [pd.to_datetime(ts) for ts in evtData['timestamp'].unique()]
            taskEngine.ringBuffer.reset()
            taskEngine.cursor = iter(frametimes)
            logging.debug(f"_feedStart({key}) frames: {len(frametimes)}, date {jreq.eventDate} evt {jreq.eventID}")
            try:
                frametime = next(taskEngine.cursor)
                while frametime < framestart:
                    frametime = next(taskEngine.cursor)
                self._get_frame(taskEngine, frametime)
            except StopIteration:
                taskEngine.cursor = None

    def _feedNext(self, taskEngine) -> None:
        if not taskEngine.ringBuffer.isFull():
            try:
                frametime = next(taskEngine.cursor)
                self._get_frame(taskEngine, frametime)
            except StopIteration:
                taskEngine.cursor = None

    def _get_frame(self, taskEngine, frametime) -> None:
        datafeed = taskEngine.dataFeed
        jreq = taskEngine.getJobRequest()
        try:
            jpeg = datafeed.get_image_jpg(jreq.eventDate, jreq.eventID, frametime)
            #taskEngine.ringBuffer.put(simplejpeg.decode_jpeg(jpeg, colorspace='BGR'))
            taskEngine.ringBuffer.put(cv2.imdecode(np.frombuffer(jpeg, dtype='uint8'), -1))
        except Exception as e:
            logging.error(f"_get_frame(), abandon cursor, ({jreq.eventDate},{jreq.eventID},{frametime}): {str(e)}")
            taskEngine.cursor = None

    def _ondeck_status(self): # debug helper
        now_ondeck = {}
        for c in self.ondeck.keys():
            if self.ondeck[c] is None:
                now_ondeck[c] = None
            else:
                now_ondeck[c] = self.ondeck[c].jobID
        return now_ondeck

    def _jobThread(self) -> None:
        logging.debug(f"Job Manager thread started")
        while not self._stop:
            # Have a task start request or job status update?
            if not taskFeed.empty():
                (tag, msg) = taskFeed.get()
                logging.debug(f"Job Manager has queue entry {(JobRequest.Status[tag],msg)}")
                #try:
                if tag == TaskEngine.TaskSUBMIT:
                    jobreq = taskList[msg]
                    jobreq.jobClass = self.taskmenu[jobreq.jobTask]['class']
                    if jobreq.jobClass in self.ondeck: 
                        if self.ondeck[jobreq.jobClass] is None: 
                            self.ondeck[jobreq.jobClass] = jobreq
                elif tag == TaskEngine.TaskSTARTED:
                    jobreq = taskList[msg]
                    self.ondeck[jobreq.jobClass] = None
                elif tag == TaskEngine.TaskCHAIN:
                    (jobid, task) = msg
                    jobreq = taskList[jobid]
                    self._chainJob(jobid, task)
                elif tag in [TaskEngine.TaskDONE,
                             TaskEngine.TaskFAIL,
                             TaskEngine.TaskCANCELED]:
                    jobreq = taskList[msg]
                    engine = self.engines[jobreq.engine]
                    if engine.jobreq.jobID == msg:
                        engine.jobreq = None
                        task_stats = (engine.get_image_cnt(), engine.get_image_rate())
                        jobreq.deregisterJOB(tag, task_stats)
                        logging.debug(f"Engine {engine.getName()} gone idle.")
                    if self.ondeck[jobreq.jobClass] == jobreq:
                        self.ondeck[jobreq.jobClass] = None
                elif tag == TaskEngine.TaskBOMB:
                    # TODO: Need an engine restart here 
                    logging.critical(f"TaskEngine '{msg}' bombed out.")
                    if msg in self.engines:
                        del self.engines[msg]
                else:
                    logging.error(f"Undefined status '{tag}' for job {msg}")
                #except TimeoutError as e:
                #    logging.error(f"JobManager timeout {e}")
                #except Exception as e:
                #    logging.error(f"JobManager unexpected exception: {e}")
                taskFeed.task_done()
                logging.debug(f"Now ondeck {str(self._ondeck_status())}")
            
            # Service the ring buffers for running tasks.
            runningTasks = 0
            for engineName in self.engines:
                engine = self.engines[engineName]
                if engine.is_alive():
                    if engine.getJobID() is not None:
                        runningTasks += 1
                        if engine.have_request():
                            (cmd, key) = engine.get_request()
                            if cmd == JobManager.ReadSTART:
                                self._feedStart(engine, key)
                                engine.send_response(engine.ringBuffer.get())
                            elif cmd == JobManager.ReadNEXT:
                                engine.ringBuffer.frame_complete()
                                engine.send_response(engine.ringBuffer.get())
                        elif engine.cursor:
                            self._feedNext(engine)
                        # TODO: Need a mechanism to cleanly shutdown a 
                        # running task in the event of DataFeed timeouts.
                else:
                    # TODO: Need an engine restart here 
                    logging.error(f"TaskEngine '{engineName}' found dead.")
                    del self.engines[engineName]

            if runningTasks < len(self.engines):
                # Have available capacity, what's on-deck by jobclass?
                for engine in self.engines.items():
                    if engine[1].getJobID() is None:
                        for jobclass in engine[1].getClasses():
                            if self.ondeck[jobclass] is not None:
                                jreq = self.ondeck[jobclass]
                                # confirm that another engine has not already been assigned this request
                                if jreq.jobID not in [taskengine.getJobID() for taskengine in self.engines.values()]:
                                    logging.debug(f"Found on deck for class {jobclass}: {jreq.jobID}")
                                    self._releaseJob(jreq.jobID, engine[0])
                                    break
                # Assign next pending job request to any open on-deck matching class. Ventilation goes here. 
                # TODO: When running in real-time, need to implement a priority based on the event start time 
                # and/or task type for waiting requests. Is it best to gather results in the order events happened, 
                # or better to give priority to the most recent event, even though the prior event has not yet been 
                # fully analyzed? i.e. Perhaps tasks for an "event in progress" should always receive the highest 
                # priority. Might not be the best approach for a fast-moving environment where multiple views could 
                # each be triggering events around the same time. Where should attention be focused? The risk is to
                # mostly be playing a game of catch-up, always behind what's happening in real time, with limited
                # understanding of what just occured. Each view could be producing requests for the same event as 
                # action progresses from one view to another. Alternatively, they could be completely unrelated, 
                # where distinct events are being simultaneously captured from one or more non-adjacent views.
                for jobclass in self.ondeck:
                    if self.ondeck[jobclass] is None:
                        with jobLock:
                            pending = [r.jobID for r in taskList.values() 
                                if r.jobStatus == JobRequest.Status_QUEUED and r.jobClass == jobclass and r.priority == 1]
                            if len(pending) == 0:
                                pending = [r.jobID for r in taskList.values() 
                                    if r.jobStatus == JobRequest.Status_QUEUED and r.jobClass == jobclass]
                        if len(pending) > 0:
                            logging.debug(f"Queue up for ondeck, class {jobclass}: {pending[0]}")
                            taskFeed.put((TaskEngine.TaskSUBMIT, pending[0]))

            if taskFeed.empty() and runningTasks == 0:
                # nothing currently running or waiting to run
                time.sleep(0.05)

    def close(self):
        self._stop = True
        self._thread.join()

async def task_loop(asyncREP, taskCFG):
    logging.info("Sentinel control loop started")
    while True:
        reply = 'OK'
        msg = await asyncREP.recv()
        payload = msg.decode("ascii")
        try:
            request = json.loads(payload)
            if 'task' in request:
                task = request['task']
                if task == 'HISTORY':
                    JobRequest.full_history_report()
                elif task == 'STATUS':  
                    pass
                    #   Stats:  Job Count, Failures, average run time
                    #   Pending jobs,  Running, Queued, Failures?
                    #       Task, Status, start time, elapsed, sink, date, event
                    #       stats grouped by task?  sink? 
                else:
                    if task in taskCFG:
                        job = JobRequest(
                            request['sink'],
                            request['node'],
                            request['date'],
                            request['event'],
                            request['pump'],
                            request['task'],
                            request.get('priority', 2)
                        )
                        taskFeed.put((JobManager.JobSUBMIT, job.jobID))
                        reply = job.jobID
                    else:
                        logging.error(f"No such task: '{task}'")
                        reply = 'Error'
            else:
                logging.error(f"Malformed task request: {request}")
                reply = 'Error'
        except ValueError as e:
            logging.error(f"JSON exception '{str(e)}' decoding task request: '{payload}'")
            reply = 'Error'
        except KeyError as keyval:
            logging.error(f"Incomplete request, '{keyval}' missing: {request}")
            reply = 'Error'
        except Exception:
            logging.exception(f"Unexpected exception processing task request")
            reply = 'Error'
        finally:
            await asyncREP.send(reply.encode("ascii"))

async def task_feedback(asyncSUB):
    while True:
        payload = await asyncSUB.recv()
        (msgTag, taskMsg) = msgpack.unpackb(payload, use_list=False)
        if msgTag == TaskEngine.TaskSTATUS:
            logging.info(str(taskMsg))
        else: 
            # These TaskEngine conditions have an equivalent mapping to JobRequest status flags
            if msgTag in [TaskEngine.TaskSTARTED,
                          TaskEngine.TaskDONE,
                          TaskEngine.TaskFAIL,
                          TaskEngine.TaskCHAIN,
                          TaskEngine.TaskCANCELED]:
                logging.debug(f"{taskMsg}: status update {JobRequest.Status[msgTag]}.")
                taskFeed.put((msgTag, taskMsg))
            elif msgTag == TaskEngine.TaskWARNING:
                logging.warning(str(taskMsg))
            elif msgTag == TaskEngine.TaskERROR:
                logging.error(str(taskMsg))
            elif msgTag == TaskEngine.TaskBOMB:
                msg = taskMsg.split(':')
                taskFeed.put(msgTag, msg[0])
                logging.critical(f"TaskEngine {taskMsg} failure.")
            else:
                logging.error(f"Unsupported task message: {msgTag}")

async def main():
    log = start_logging(CFG["logging_port"])
    asyncREP = ctxAsync.socket(zmq.REP)  # task loop control socket
    asyncSUB = ctxAsync.socket(zmq.SUB)  # subscriptions for job result publishers
    asyncREP.bind(f"tcp://*:{CFG['control_port']}")
    asyncSUB.subscribe(b'')
    manager = JobManager(CFG["task_engines"], 
                         CFG["ring_buffer_models"],
                         CFG["task_list"],
                         CFG["default_pump"],
                         asyncSUB)
    try:
        log.info("Sentinel started")
        await asyncio.gather(task_loop(asyncREP, CFG["task_list"]), 
                             task_feedback(asyncSUB))
    except (KeyboardInterrupt, SystemExit):
        log.warning('Ctrl-C was pressed or SIGTERM was received')
    except Exception as e:  # traceback will appear in log 
        log.exception('Unanticipated error with no exception handler')
    finally:
        manager.close()
        asyncREP.close()
        asyncSUB.close()
        log.info("Sentinel shutdown")

def start_logging(publish):
    logconfig = CFG['logconfig']
    socket = ctxBlocking.socket(zmq.PUB)
    socket.bind(f"tcp://*:{publish}") 
    logconfig['handlers']['zmq']['interface_or_socket'] = socket
    logging.config.dictConfig(logconfig)
    log = logging.getLogger()
    return log

if __name__ == '__main__':
    asyncio.run(main())
