"""sentinel: The heart of the SentinelCam distributed vision engine. 

Copyright (c) 2023 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import asyncio
import json
import logging
import logging.handlers
import cv2
import numpy as np
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
import simplejpeg

CFG = readConfig(os.path.join(os.path.expanduser("~"), "sentinel.yaml"))
SOCKDIR = CFG["socket_dir"]

ctxAsync = AsyncContext.instance()
ctxBlocking = zmq.Context.shadow(ctxAsync.underlying)
jobLock = threading.Lock()
taskFeed = queue.Queue()

taskList = {}
jobList = {}
 
class JobRequest:
    
    Status_UNDEFINED = 0
    Status_QUEUED = 1
    Status_RUNNING = 2 
    Status_DONE = 3
    Status_FAILED = 4
    Status_CANCELED = 5

    Status = ["Undefined", "Queued", "Running", "Done", "Failed", "Canceled"]

    def __init__(self, sink, node, date, event, pump, taskname) -> None:
        self.jobID = uuid.uuid1().hex
        self.jobTask = taskname
        self.jobClass = 1
        self.jobStatus = JobRequest.Status_QUEUED
        self.jobSubmitTime = datetime.utcnow()
        self.jobStartTime = None
        self.jobEndTime = None
        self.sourceNode = node
        self.dataSink = sink
        self.eventDate = date
        self.eventID = event
        self.datapump = pump  # datapump connection string
        self.camsize = (0,0)  # TODO: required per event; learn up front, dynamically
        self.engine = None
        with jobLock:
            taskList[self.jobID] = self

    def registerJOB(self, engine) -> None:
        self.jobStartTime = datetime.utcnow()
        self.jobStatus = JobRequest.Status_RUNNING
        self.engine = engine
        with jobLock:
            jobList[self.jobID] = self

    def deregisterJOB(self, status) -> None:
        self.jobEndTime = datetime.utcnow()
        self.jobStatus = status
        logging.info(self.eoj_Message())
        with jobLock:
            if self.jobID in jobList:
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

    def eoj_Message(self) -> tuple:
        (start_time, end_time, elapsed_time) = self._timeVals()
        result = (
            'EOJ',
            self.jobTask,
            self.sourceNode,
            JobRequest.Status[self.jobStatus],
            elapsed_time,
            self.engine,
            self.dataSink,
            self.eventDate,
            self.eventID
        )
        return result

    def summary_JSON(self) -> str:
        (start_time, end_time, elapsed_time) = self._timeVals()
        return json.dumps({
            'Node': self.sourceNode,
            'Date': self.eventDate,
            'Task': self.jobTask,
            'DataSink': self.dataSink,
            'Status': JobRequest.Status[self.jobStatus],
            'SubmitTime': self.jobSubmitTime.isoformat(),
            'StartTime': start_time,
            'EndTime': end_time,
            'ElapsedTime': elapsed_time,
            'TaskEngine': self.engine,
            'EventID': self.eventID,
            'JobID': self.jobID
        })

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

    taskQ : multiprocessing.Managers.SyncManager.Queue 
        Used for sending job requests to the task engine.

    taskFlag : multiprocessing.Managers.SyncManager.Value 
        Implements a shared flag that can be used to cancel a running task.

    rawRingbuff : dict
        The image frame ring buffers for this task engine. Each item is a list of shared 
        memory blocks keyed by image size. These are a multiprocessing.sharedctypes.RawArray, 
        each will be redefined as a NumPy array by the child process. 
    """

    def __init__(self, engineName, pump, taskCFG, accelerator, taskQ, taskFlag, rawRingbuff) -> None:
        self._engine = engineName
        self._taskQ = taskQ
        self._taskFlag = taskFlag
        self._rawRingBuffer = rawRingbuff
        self.process = multiprocessing.Process(target=self.taskHost, args=(
            engineName, pump, taskCFG, accelerator, taskQ, taskFlag, rawRingbuff))
        self.process.start()

    def terminate(self) -> None:
        if self.process.is_alive():
            self.process.kill()
            self.process.join()

    # --------------------------------------------------------------------------------------------------
    def taskHost(self, engineName, pump, taskCFG, accelerator, taskQ, taskFlag, _ringbuff):
    # --------------------------------------------------------------------------------------------------
        try:
            taskpump = pump
            feed = DataFeed(taskpump)                        # useful for task-specific datapump access
            ringWire = feed.zmq_context.socket(zmq.REQ)      # IPC signaling for ring buffer control
            publisher = feed.zmq_context.socket(zmq.PUB)     # job result publication
            ringWire.connect(f"ipc://{SOCKDIR}/{engineName}")
            publisher.bind(f"ipc://{SOCKDIR}/{engineName}.PUB")
            ringWire.send(msgpack.packb(0))  # send the ready handshake
            handshake = ringWire.recv()  # wait for subscriber to connect

            self.frame_start = None
            self.frame_offset = 0

            def ringStart(frametime) -> int:
                timestamp = frametime.isoformat()
                self.frame_start = timestamp
                self.frame_offset = 0
                start_command = (JobManager.ReadSTART, 
                                "{}_{}".format(timestamp[:10], timestamp[11:].replace(':','.')))
                ringWire.send(msgpack.packb(start_command))
                bucket = msgpack.unpackb(ringWire.recv())
                return bucket

            def ringNext() -> int:
                self.frame_offset += 1
                ringWire.send(msgpack.packb((JobManager.ReadNEXT, None)))
                bucket = msgpack.unpackb(ringWire.recv())
                return bucket

            def publish(msg, frameref = False) -> None:
                if frameref:
                    frame = (self.frame_start, self.frame_offset)
                    msg = frame + msg
                envelope = (TaskEngine.TaskSTATUS, msg)
                publisher.send(msgpack.packb(envelope))

            failCnt = 0
            jobreq = None
            imagesize = (0,0)
            ringbuff = []

            while failCnt < TaskEngine.FAIL_LIMIT:
                if taskQ.empty():
                    time.sleep(1)
                else:
                    jobreq = taskQ.get()
                    eoj_status = TaskEngine.TaskDONE  # assume success
                    try:
                        taskQ.task_done()
                        self.start_time = time.time()
                        if jobreq.datapump != taskpump:
                            taskpump = jobreq.datapump
                            feed.zmq_socket.connect(taskpump)
                        if jobreq.camsize != imagesize:
                            imagesize = jobreq.camsize
                            dtype = np.dtype('uint8')
                            shape = (imagesize[1], imagesize[0], 3)
                            ringbuff = [np.frombuffer(buffer, dtype=dtype).reshape(shape) for buffer in _ringbuff[imagesize]]

                        taskFlag.value = TaskEngine.TaskSTARTED
                        startMsg = (TaskEngine.TaskSTARTED, jobreq.jobID)
                        publisher.send(msgpack.packb(startMsg))

                        # ----------------------------------------------------------------------
                        #   Task Initialization
                        # ----------------------------------------------------------------------
                        trackingData = feed.get_tracking_data(jobreq.eventDate, jobreq.eventID)
                        taskcfg = taskCFG[jobreq.jobTask]
                        task = TaskFactory(jobreq, trackingData, feed, taskcfg["config"], accelerator)
                        # Hang hooks for task references to ring buffer and publisher
                        task.ringStart = ringStart
                        task.ringNext = ringNext
                        task.publish = publish

                        # ----------------------------------------------------------------------
                        #   Start the Ring Buffer
                        # ----------------------------------------------------------------------
                        frame_start = trackingData.iloc[0].timestamp  # default to the first frame
                        bucket = ringStart(frame_start)

                        # ----------------------------------------------------------------------
                        #   Frame loop for an image pipeline task
                        # ----------------------------------------------------------------------
                        while bucket != JobManager.ReadEOF:
                            if task.pipeline(ringbuff[bucket]):
                                bucket = ringNext()
                            else:
                                bucket = JobManager.ReadEOF

                            if taskFlag.value == TaskEngine.TaskCANCELED:
                                eoj_status = TaskEngine.TaskCANCELED
                                break

                        # ----------------------------------------------------------------------
                        #   Publish final results 
                        # ----------------------------------------------------------------------
                        task.finalize()

                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except cv2.error as e:
                        msg = (TaskEngine.TaskSTATUS, "OpenCV error, {}".format(str(e)))
                        publisher.send(msgpack.packb(msg))
                        eoj_status = TaskEngine.TaskFAIL
                        failCnt += 1
                    except Exception as e:
                        msg = (TaskEngine.TaskSTATUS, "taskHost() exception, {}".format(str(e)))
                        publisher.send(msgpack.packb(msg))
                        eoj_status = TaskEngine.TaskFAIL
                        failCnt += 1
                    else:
                        failCnt = 0
                    finally:
                        publisher.send(msgpack.packb((eoj_status, jobreq.jobID)))
            
            # Limit on successive failures exceeded
            msg = (TaskEngine.TaskBOMB, f"{engineName}: JobTasking failure limit exceeded.")
            publisher.send(msgpack.packb(msg))

        except (KeyboardInterrupt, SystemExit):
            print(f"JobTasking shutdown {engineName}.")
        except Exception as e:
            msg = (TaskEngine.TaskBOMB, "{}: JobTasking failure, {}".format(engineName, str(e)))
            publisher.send(msgpack.packb(msg))
            traceback.print_exc()  # see syslog for traceback
        finally:
            feed.close()
            ringWire.close()
            publisher.close()
        # ----------------------------------------------------------------------
        #   End of TaskEngine
        # ----------------------------------------------------------------------

class TaskEngine:

    FAIL_LIMIT = 3

    TaskSTATUS = 0
    TaskSUBMIT = 1
    TaskSTARTED = 2
    TaskDONE = 3
    TaskFAIL = 4
    TaskCANCELED = 5
    TaskBOMB = -1

    def __init__(self, engineName, config, ringCFG, taskCFG, manager, pump, asyncSUB) -> None:
        self.job_classes = config["classes"]
        self.accelerator = config["accelerator"]
        self.taskCFG = taskCFG
        self.taskQ = manager.Queue()
        self.taskFlag = manager.Value('i', TaskEngine.TaskDONE)
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
        self._engine = JobTasking(engineName, pump, taskCFG, self.accelerator, self.taskQ, self.taskFlag, self.rawBuffers)
        # establish handshake with child, connect to result publisher before continuing
        handshake = self.wire.recv()
        asyncSUB.connect(f"ipc://{SOCKDIR}/{engineName}.PUB")
        self.wire.send(handshake)

    def getJobID(self) -> str:
        if self.jobreq:
            return self.jobreq.jobID
        else:
            return None

    def start_job(self, jobreq) -> bool:
        confirm_start = True
        if self.imagesize != jobreq.camsize:
            if jobreq.camsize in self.ringbuffers:
                self.imagesize = jobreq.camsize
                self.ringBuffer = self.ringbuffers[self.imagesize]
            else:
                logging.error(f"RingBuffer definition for {jobreq.camsize} not supported by task engine {jobreq.engine}.")
                confirm_start = False
        if confirm_start:
            self.jobreq = jobreq
            self.taskQ.put(jobreq)
            logging.debug(f"Queued job start {jobreq.jobID}")
        return confirm_start

    def have_request(self) -> bool:
        return self.wire.ready()

    def get_request(self) -> tuple:
        return self.wire.recv()

    def send_response(self, resp) -> None:
        self.wire.send(resp)

    def is_alive(self) -> bool:
        return self._engine.process.is_alive()

    def cancel(self) -> None:
        self.taskFlag.value = TaskEngine.TaskCANCELED

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
        self.engines = {}
        self.datafeeds = {}
        self.manager = multiprocessing.Manager()
        for engine in engineCFG:
            self.engines[engine] = TaskEngine(engine, engineCFG[engine], ringCFG, taskCFG, self.manager, default_pump, _asyncSUB)
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
        jreq = taskList[jobid]
        jreq.registerJOB(engine)
        self.engines[engine].dataFeed = self._setPump(jreq.datapump)
        framesize = self._getFrameDimensons(jreq)
        jreq.camsize = (framesize[1], framesize[0])
        if not self.engines[engine].start_job(jreq):
            jreq.deregisterJOB(TaskEngine.TaskFAIL)

    def _getFrameDimensons(self, jreq) -> tuple:
        # TODO: Eliminate this stupid hack. Image dimsensions should be carried in the camera event data.
        datafeed = self.datafeeds[jreq.datapump]
        frametimes = datafeed.get_image_list(jreq.eventDate, jreq.eventID)
        jpeg = datafeed.get_image_jpg(jreq.eventDate, jreq.eventID, frametimes[0])
        frame = simplejpeg.decode_jpeg(jpeg, colorspace='BGR')
        logging.debug(f"Learned image dimensions: {frame.shape}")
        return frame.shape

    def _feedStart(self, taskEngine, key) -> None:
        jreq = taskEngine.jobreq
        datafeed = taskEngine.dataFeed
        taskEngine.ringBuffer.reset()
        framestart = datetime.strptime(key, "%Y-%m-%d_%H.%M.%S.%f")
        frametimes = datafeed.get_image_list(jreq.eventDate, jreq.eventID)
        taskEngine.cursor = iter(frametimes)
        logging.debug(f"Feed starting for {framestart}")
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

    def _get_frame(self, taskEngine, frametime):
        datafeed = taskEngine.dataFeed
        task = taskEngine.jobreq
        jpeg = datafeed.get_image_jpg(task.eventDate, task.eventID, frametime)
        taskEngine.ringBuffer.put(simplejpeg.decode_jpeg(jpeg, colorspace='BGR'))

    def _jobThread(self):
        logging.debug(f"Job Manager thread started.")
        while not self._stop:
            if not taskFeed.empty():
                (tag, msg) = taskFeed.get()
                if tag == TaskEngine.TaskSUBMIT:
                    jobreq = taskList[msg]
                    task = self.taskmenu[jobreq.jobTask]
                    for engine in self.engines.items():
                        if engine[1].getJobID() is None and task['class'] in engine[1].job_classes: 
                            self._releaseJob(jobreq.jobID, engine[0])
                            break
                elif tag in [TaskEngine.TaskDONE,
                             TaskEngine.TaskFAIL,
                             TaskEngine.TaskCANCELED]:
                    engine = self.engines[taskList[msg].engine]
                    engine.jobreq = None
                    engine.taskFlag.value = tag
                    taskList[msg].deregisterJOB(tag)
                elif tag == TaskEngine.TaskBOMB:
                    # TODO: Need an engine restart here 
                    logging.error(f"TaskEngine '{msg}' bombed out.")
                    if msg in self.engines:
                        del self.engines[msg]
                else:
                    logging.error(f"Undefined status '{tag}' for job {msg}")
                taskFeed.task_done()
            
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
                    if engine.cursor:
                        self._feedNext(engine)
                else:
                    # TODO: Need an engine restart here 
                    logging.error(f"TaskEngine '{engineName}' found dead.")
                    if engineName in self.engines:
                        del self.engines[engineName]

            if runningTasks < len(self.engines):
                # Have available capacity, any queued jobs awaiting release?  
                with jobLock:
                    pending = [r.jobID for r in taskList.values() if r.jobStatus == JobRequest.Status_QUEUED]
                if len(pending) > 0:
                    taskFeed.put((TaskEngine.TaskSUBMIT, pending[0]))
            if runningTasks == 0:
                time.sleep(1)

    def close(self):
        self._stop = True
        self._thread.join()

async def task_loop(asyncREP, taskCFG):
    logging.debug("Sentinel control loop started.")
    while True:
        reply = 'OK'
        msg = await asyncREP.recv()
        payload = msg.decode("ascii")
        try:
            request = json.loads(payload)
            if 'task' in request:
                # task could be a command, something like: STATUS, CANCEL, HISTORY
                task = request['task']
                if task == 'HISTORY':
                    with jobLock:
                        for jobreq in taskList.values():
                            logging.info(jobreq.summary_JSON())
                else:
                    if task in taskCFG:
                        job = JobRequest(
                            request['sink'],
                            request['node'],
                            request['date'],
                            request['event'],
                            request['pump'],
                            request['task']
                        )
                        logging.debug(f"Queued job: {job.jobTask}, {job.eventID}")
                        taskFeed.put((JobManager.JobSUBMIT, job.jobID))
                    else:
                        logging.error(f"No such task: '{task}'")
                        reply = 'Error'
            else:
                logging.error(f"Malformed task request: {request}")
                reply = 'Error'
        except ValueError as e:
            logging.error(f"JSON exception '{str(e)}' decoding task request: '{payload}'")
            reply = 'Error'
        except KeyError:
            logging.error(f"Incomplete request: {request}")
            reply = 'Error'
        except Exception as e:
            logging.exception(f"Unexpected exception: {str(e)}")
            traceback.print_exc()
            reply = 'Error'
        finally:
            await asyncREP.send(reply.encode("ascii"))

async def task_feedback(asyncSUB):
    while True:
        payload = await asyncSUB.recv()
        (msgTag, taskMsg) = msgpack.unpackb(payload, use_list=False)
        if msgTag == TaskEngine.TaskSTATUS:
            logging.info(taskMsg)
        else: 
            # These TaskEngine conditions have an equivalent mapping to JobRequest status flags
            if msgTag in [TaskEngine.TaskSTARTED,
                          TaskEngine.TaskDONE,
                          TaskEngine.TaskFAIL,
                          TaskEngine.TaskCANCELED]:
                if msgTag != TaskEngine.TaskSTARTED:
                    logging.debug(f"{taskMsg}: job task {JobRequest.Status[msgTag]}.")
                    taskFeed.put((msgTag, taskMsg))
            elif msgTag == TaskEngine.TaskBOMB:
                msg = taskMsg.split(':')
                taskFeed.put(msgTag, msg[0])
                logging.error(f"TaskEngine {taskMsg} failure.")
            else:
                logging.error(f"Unsupported task message: {msgTag}")

async def main():
    log = start_logging(CFG["log_dir"])
    log.info("Sentinel started.")
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
        await asyncio.gather(task_loop(asyncREP, CFG["task_list"]), 
                             task_feedback(asyncSUB))
    except (KeyboardInterrupt, SystemExit):
        log.warning('Ctrl-C was pressed or SIGTERM was received')
    except Exception as e:  
        log.exception(f'Unhandled exception: {str(e)}')
    finally:
        manager.close()
        asyncREP.close()
        asyncSUB.close()
        log.info("Sentinel shutdown")

def start_logging(logdir):
    log = logging.getLogger()
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(logdir, 'sentinel.log'),
        maxBytes=1048576, backupCount=10)
    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log

if __name__ == '__main__' :
    asyncio.run(main())
