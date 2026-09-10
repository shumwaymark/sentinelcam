"""sentinel: The heart of the SentinelCam distributed vision engine.

Copyright (c) 2023 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import json
import signal
import time
import logging
import logging.config
import traceback
import threading
import queue
import asyncio
import multiprocessing
from multiprocessing import sharedctypes
from zmq.asyncio import Context as AsyncContext
from collections import deque
from datetime import datetime, timedelta
from ast import literal_eval
import cv2
import numpy as np
import pandas as pd
import uuid
import zmq
from sentinelcam.datafeed import DataFeed
from sentinelcam.taskfactory import TaskFactory
from sentinelcam.utils import readConfig
from sentinel_state import save_state, load_state
import msgpack
import simplejpeg

CFG = readConfig(os.path.join(os.path.expanduser("~"), "sentinel.yaml"))
SOCKDIR = CFG["socket_dir"]

ctxAsync = AsyncContext.instance()
ctxBlocking = zmq.Context.shadow(ctxAsync.underlying)
jobLock = threading.Lock()
taskFeed = queue.Queue()
shutdown_event = None  # asyncio.Event, created in main()

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
        self.ring_start_avg = 0.0
        self.ring_next_avg = 0.0
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
        logging.info(self.summary_JSON())
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
            'rate': self.image_rate,
            'ring_start_avg': round(self.ring_start_avg, 6),
            'ring_next_avg': round(self.ring_next_avg, 6)
        })

    def to_dict(self) -> dict:
        """Export all serializable fields for state checkpoint."""
        (start_time, end_time, elapsed_time) = self._timeVals()
        return {
            'node': self.sourceNode,
            'date': self.eventDate,
            'task': self.jobTask,
            'class': self.jobClass,
            'sink': self.dataSink,
            'status': JobRequest.Status[self.jobStatus],
            'submitted': self.jobSubmitTime.isoformat(),
            'started': start_time,
            'ended': end_time,
            'elapsed': elapsed_time,
            'engine': self.engine,
            'priority': self.priority,
            'images': self.image_cnt,
            'rate': self.image_rate,
            'event': self.eventID,
            'pump': self.datapump,
            'ring_start_avg': round(self.ring_start_avg, 6),
            'ring_next_avg': round(self.ring_next_avg, 6),
        }

    @classmethod
    def from_dict(cls, jobid, fields) -> 'JobRequest':
        """Reconstruct a JobRequest from serialized fields.

        Bypasses __init__ to avoid logging and taskList mutation.
        Caller is responsible for inserting into taskList.
        """
        obj = object.__new__(cls)
        obj.jobID = jobid
        obj.jobTask = fields.get('task')
        obj.jobClass = fields.get('class')
        obj.sourceNode = fields.get('node')
        obj.dataSink = fields.get('sink')
        obj.eventDate = fields.get('date')
        obj.eventID = fields.get('event')
        obj.datapump = fields.get('pump')
        obj.engine = fields.get('engine')
        obj.priority = fields.get('priority', 2)
        obj.image_cnt = fields.get('images', 0)
        obj.image_rate = fields.get('rate', 0.0)
        obj.ring_start_avg = fields.get('ring_start_avg', 0.0)
        obj.ring_next_avg = fields.get('ring_next_avg', 0.0)
        obj.camsize = (0, 0)
        # Status: convert string back to int constant
        status_str = fields.get('status', 'Undefined')
        try:
            obj.jobStatus = JobRequest.Status.index(status_str)
        except ValueError:
            obj.jobStatus = JobRequest.Status_UNDEFINED
        # Datetime fields: already converted by deserialize_state
        submitted = fields.get('submitted')
        obj.jobSubmitTime = submitted if isinstance(submitted, datetime) else datetime.now()
        started = fields.get('started')
        obj.jobStartTime = started if isinstance(started, datetime) else None
        ended = fields.get('ended')
        obj.jobEndTime = ended if isinstance(ended, datetime) else None
        return obj

    def full_history_report() -> None:
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

    def close(self) -> None:
        self._poller.unregister(self._wire)
        self._wire.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

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

    def __init__(self, engineName, pump, taskCFG, accelerator, taskQ, rawRingbuff, ringLatency) -> None:
        self._engine = engineName
        self._taskQ = taskQ
        self._rawRingBuffer = rawRingbuff
        self._ringLatency = ringLatency
        self.process = multiprocessing.Process(target=self.taskHost, args=(
            engineName, pump, taskCFG, accelerator, taskQ, rawRingbuff, ringLatency))
        self.process.start()

    def terminate(self) -> None:
        if self.process.is_alive():
            self.process.kill()
            self.process.join()

    # --------------------------------------------------------------------------------------------------
    def taskHost(self, engineName, pump, taskCFG, accelerator, taskQ, _ringbuff, ringLatency):
    # --------------------------------------------------------------------------------------------------
        # The parent owns this child's lifecycle, and reaps it with kill() -- see
        # JobTasking.terminate(). Ignore SIGTERM explicitly so that a stop signal delivered
        # to the whole control group (systemd's default KillMode) cannot cut a running task
        # out from under the graceful drain, which needs its engines alive to finish the
        # work in flight before state is serialized.
        #
        # This has been the behavior all along, but only by accident: the child is forked
        # after loop.add_signal_handler(SIGTERM, ...) installs a no-op handler in the parent,
        # and inherits it. Forking engines earlier, or reworking the signal wiring, would
        # have silently removed the protection -- with jobs disappearing on restart as the
        # only symptom. Make it explicit and independent of that ordering.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            ring_start_sum, ring_start_max, ring_start_count, ring_next_sum, ring_next_max, ring_next_count = ringLatency
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
                t0 = time.monotonic()
                ringWire.send(msgpack.packb(_start_command))
                if newEvent:
                    # wait here for confirmation of ring buffer assignment
                    self.jobreq = taskQ.get()
                    if self.jobreq.camsize != self.imagesize and self.jobreq.camsize != (0,0):
                        self.imagesize = self.jobreq.camsize
                        self.ringbuff = ringbuffers[self.imagesize]
                bucket = msgpack.unpackb(ringWire.recv())
                dt = time.monotonic() - t0
                ring_start_sum.value += dt
                ring_start_count.value += 1
                if dt > ring_start_max.value:
                    ring_start_max.value = dt
                return bucket

            def ringNext() -> int:
                self.frame_offset += 1
                t0 = time.monotonic()
                ringWire.send(msgpack.packb((JobManager.ReadNEXT, None)))
                bucket = msgpack.unpackb(ringWire.recv())
                dt = time.monotonic() - t0
                ring_next_sum.value += dt
                ring_next_count.value += 1
                if dt > ring_next_max.value:
                    ring_next_max.value = dt
                return bucket

            def getRing() -> list:
                return self.ringbuff

            def publish(msg, frameref=None, cwUpd=False, offset_override=None) -> None:
                if frameref is not None:
                    # Use offset_override if provided (for batch tasks with timestamp mapping)
                    offset = offset_override if offset_override is not None else self.frame_offset
                    frame = (self.jobreq.jobID, frameref, self.ringctrl, self.frame_start, offset)
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
                    task = None
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
                            image_timestamps = None
                        else:
                            # preload desired tracking set and retrieve starting image
                            self.trktype = taskcfg.get('trk_type', 'trk')
                            self.ringctrl = taskcfg.get('ringctrl', 'full')
                            trackingData = feed.get_tracking_data(self.jobreq.eventDate, self.jobreq.eventID, self.trktype)
                            # Get full image timestamp list for batch tasks to map timestamps to offsets
                            image_timestamps = feed.get_image_list(self.jobreq.eventDate, self.jobreq.eventID)
                            if self.ringctrl == 'trk':
                                startframe = trackingData.iloc[0]['timestamp']
                            else:
                                startframe = image_timestamps[0]

                        if 'alias' in taskcfg:
                            self.jobreq.jobTask = taskcfg['alias']
                        if 'chain' in taskcfg:
                            nextTask = taskcfg['chain']

                        task = TaskFactory(self.jobreq, trackingData, feed, taskcfg["config"], accelerator, image_timestamps)
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
                        if task.finalize():
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
                    except TimeoutError as e:
                        msg = (TaskEngine.TaskERROR, str(e))
                        publisher.send(msgpack.packb(msg))
                        eoj_status = TaskEngine.TaskFAIL
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
                        # Release the task -- and with it any accelerator handle it
                        # owns -- BEFORE the next one is constructed. On a Coral
                        # engine each task's __init__ calls make_interpreter(), which
                        # opens the EdgeTPU; because Python evaluates the right-hand
                        # side of `task = TaskFactory(...)` before rebinding, the
                        # previous task's interpreter would otherwise still hold the
                        # single-context device while the next one opens it and swaps
                        # the on-chip model. GetFaces, second in the standard chain
                        # behind MobileNetSSD_allFrames, hit that window on every
                        # event -- which is why it was the task that wedged and object
                        # detection never was. Refcounting frees it here immediately.
                        task = None

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

class EngineHealth:
    def __init__(self, cooldown=300):
        self.job_count = 0
        self.recent_jobs = deque(maxlen=10)  # (task, images, rate, status, elapsed)
        self.consecutive_failures = 0
        self.consecutive_low_frames = 0
        self.last_restart = None        # datetime
        self.total_restarts = 0
        self.restart_cooldown = cooldown  # seconds between automatic restarts

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
    TaskRESTART = 9
    TaskBOMB = -1

    def __init__(self, engineName, config, ringCFG, taskCFG, pump, asyncSUB) -> None:
        self.name = engineName
        self.job_classes = config["classes"]
        self.accelerator = config["accelerator"]
        self.taskCFG = taskCFG
        self._asyncSUB = asyncSUB
        self.restart_count = 0
        _hcfg = CFG.get('health', {})
        self.health = EngineHealth(cooldown=_hcfg.get('restart_cooldown', 300))
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
        # Ring buffer latency tracking (child writes, parent reads at EOJ)
        # ring_start: cost of ReadSTART (includes DataPump fetch — known high cost)
        # ring_next: cost of ReadNEXT (pure _jobThread response time — the health signal)
        self._ring_start_sum = multiprocessing.Value('d', 0.0)
        self._ring_start_max = multiprocessing.Value('d', 0.0)
        self._ring_start_count = multiprocessing.Value('L', 0)
        self._ring_next_sum = multiprocessing.Value('d', 0.0)
        self._ring_next_max = multiprocessing.Value('d', 0.0)
        self._ring_next_count = multiprocessing.Value('L', 0)
        self._ringLatency = (self._ring_start_sum, self._ring_start_max, self._ring_start_count,
                             self._ring_next_sum, self._ring_next_max, self._ring_next_count)
        # Ready to fork() the child subprocess for this task engine:
        self._engine = JobTasking(engineName, pump, taskCFG, self.accelerator, self.taskQ, self.rawBuffers, self._ringLatency)
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
            self.task_start = time.monotonic()
            self.image_cnt = 0
            # Reset ring latency accumulators for this job
            self._ring_start_sum.value = 0.0
            self._ring_start_max.value = 0.0
            self._ring_start_count.value = 0
            self._ring_next_sum.value = 0.0
            self._ring_next_max.value = 0.0
            self._ring_next_count.value = 0
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
        return round((self.get_image_cnt() / (time.monotonic() - self.task_start)), 2)

    def get_ring_latency(self) -> tuple:
        """Read ring latency from shared memory. Returns (start_avg, start_max, next_avg, next_max)."""
        sc = self._ring_start_count.value
        nc = self._ring_next_count.value
        start_avg = self._ring_start_sum.value / sc if sc > 0 else 0.0
        next_avg = self._ring_next_sum.value / nc if nc > 0 else 0.0
        return (start_avg, self._ring_start_max.value, next_avg, self._ring_next_max.value)

    def is_alive(self) -> bool:
        return self._engine.process.is_alive()

    def cancel(self) -> None:
        # TODO: cancel a running task
        #self.taskFlag.value = TaskEngine.TaskCANCELED
        pass

    def restart(self, pump) -> bool:
        """Restart the TaskEngine child process. Called from _jobThread context.

        Fails any in-flight job, tears down the child and IPC sockets,
        resets ring buffers, then forks a fresh child with full handshake.

        Returns True on success, False on failure.
        """
        engineName = self.name
        logging.warning(f"TaskEngine '{engineName}' restart initiated")

        # Step 1: Fail any in-flight job before touching sockets or processes
        if self.jobreq is not None:
            try:
                task_stats = (self.get_image_cnt(), self.get_image_rate())
            except Exception:
                task_stats = (0, 0.0)
            self.jobreq.deregisterJOB(JobRequest.Status_FAILED, task_stats)
            self.jobreq = None
        self.cursor = None

        # Step 2: Kill the child process
        self._engine.terminate()

        # Step 3: Tear down parent-side IPC sockets
        self.wire.close()
        try:
            self._asyncSUB.disconnect(f"ipc://{SOCKDIR}/{engineName}.PUB")
        except zmq.ZMQError:
            pass  # Already disconnected or never connected

        # Step 4: Clean up IPC socket files on disk
        for suffix in ['', '.PUB']:
            try:
                os.remove(f"{SOCKDIR}/{engineName}{suffix}")
            except FileNotFoundError:
                pass

        # Step 5: Reset RingBuffer state (shared memory stays allocated)
        for rb in self.ringbuffers.values():
            rb.reset()
        self.imagesize = (0, 0)
        self.ringBuffer = None
        self.dataFeed = None

        # Step 5.5: Reset ring latency accumulators
        self._ring_start_sum.value = 0.0
        self._ring_start_max.value = 0.0
        self._ring_start_count.value = 0
        self._ring_next_sum.value = 0.0
        self._ring_next_max.value = 0.0
        self._ring_next_count.value = 0

        # Step 6: Create fresh multiprocessing.Queue
        self.taskQ = multiprocessing.Queue()

        # Step 7: Create fresh RingWire (REP socket, must exist before child fork)
        self.wire = RingWire(SOCKDIR, engineName)

        # Step 8: Fork new JobTasking child process
        self._engine = JobTasking(
            engineName, pump, self.taskCFG, self.accelerator,
            self.taskQ, self.rawBuffers, self._ringLatency)

        # Step 9: Complete handshake with timeout guard (10 seconds)
        poller = zmq.Poller()
        poller.register(self.wire._wire, zmq.POLLIN)
        events = dict(poller.poll(10000))
        if self.wire._wire not in events:
            logging.critical(f"TaskEngine '{engineName}' restart failed: handshake timeout")
            self.wire.close()
            self._engine.terminate()
            return False
        handshake = self.wire.recv()
        self._asyncSUB.connect(f"ipc://{SOCKDIR}/{engineName}.PUB")
        self.wire.send(handshake)

        # Step 10: Log success
        logging.warning(f"TaskEngine '{engineName}' restarted successfully")
        return True

class SnapshotRequest:
    """Thread-safe state snapshot mechanism for the JobManager. Used for STATUS,
    MAINTENANCE, and SHUTDOWN requests. The task_loop async coroutine creates a
    SnapshotRequest, puts it on taskFeed, and awaits the event via run_in_executor.
    The JobManager thread processes the request safely within its own context, populates
    the result, and sets the event.
    """
    def __init__(self, kind):
        self.kind = kind       # 'STATUS', 'MAINTENANCE', 'SHUTDOWN'
        self.event = threading.Event()
        self.result = None

class JobManager:

    JobSTATUS = 0
    JobSUBMIT = 1
    JobSTART = 2
    JobCANCEL = 3
    JobSNAPSHOT = 20

    ReadSTART = 10
    ReadNEXT = 11
    ReadEOF = -1
    ReadNOP = 0

    def __init__(self, engineCFG, ringCFG, taskCFG, default_pump, _asyncSUB,
                 recovered_health=None) -> None:
        self.ondeck = {}
        self.engines = {}
        self.datafeeds = {}
        self._default_pump = default_pump
        self._asyncSUB = _asyncSUB
        # Health heuristic configuration
        _hcfg = CFG.get('health', {})
        # Coral degradation is a RATE collapse, judged only on jobs long enough for
        # the rate to mean anything (a short job's rate is dominated by model-load
        # overhead). Measured on Alpha over 888 completed jobs: with min_rate_frames
        # 20, the healthy 2nd percentile is 16.5 fps and the median 26.0, so 8.0 fps
        # is a genuine collapse -- exactly 1 of 770 healthy jobs sits below it, and
        # strike_limit requires three in a row.
        self._low_rate_threshold = _hcfg.get('low_rate_threshold', 8.0)
        self._min_rate_frames = _hcfg.get('min_rate_frames', 20)
        # Wall-clock ceiling on a running job; 0 disables. Per-task `runtime_limit`
        # in task_list overrides it.
        self._job_runtime_limit = _hcfg.get('job_runtime_limit', 0)
        self._strike_limit = _hcfg.get('strike_limit', 3)
        self._fail_strike_limit = _hcfg.get('fail_strike_limit', 5)
        self._max_auto_restarts = _hcfg.get('max_auto_restarts', 10)
        self._queue_depth = 0
        self._queue_hwm = 0
        self._queue_latency = 0.0
        self._queue_latency_hwm = 0.0
        self._running_jobs = 0
        self._jobs_completed = 0
        self._jobs_failed = 0
        self._start_time = datetime.now()
        # --- Queue diagnostics ---
        self._depth_hwm_snapshots = deque(maxlen=10)
        self._latency_hwm_snapshots = deque(maxlen=10)
        self._queue_by_class = {}
        self._queue_by_class_hwm = {}
        self._submission_times = deque()
        self._submission_window = 300
        self._engine_busy_start = {}
        self._engine_busy_total = {}
        # --- State maintenance ---
        _mcfg = CFG.get('maintenance', {})
        self._state_filepath = os.path.expanduser(
            _mcfg.get('state_file', '~/sentinel/state.json'))
        self._checkpoint_interval = _mcfg.get('checkpoint_interval', 50)
        self._retention_hours = _mcfg.get('retention_hours', 48)
        self._checkpoint_counter = 0
        # --- Graceful shutdown ---
        _scfg = CFG.get('shutdown', {})
        self._drain_timeout = _scfg.get('drain_timeout', 60)
        self._draining = threading.Event()
        for engine in engineCFG:
            self.engines[engine] = TaskEngine(engine, engineCFG[engine], ringCFG, taskCFG, default_pump, _asyncSUB)
            self.ondeck[engine] = None
            self._engine_busy_total[engine] = 0.0
        # Restore engine health counters from recovered state
        if recovered_health:
            for name, health_data in recovered_health.items():
                if name in self.engines:
                    eng = self.engines[name]
                    lr = health_data.get('last_restart')
                    eng.health.last_restart = lr if isinstance(lr, datetime) else None
                    logging.info(f"Engine '{name}' health restored: "
                                 f"{eng.health.total_restarts} prior restarts")
                    # Reset total_restarts from recovered state to avoid exceeding auto-restart limit
                    eng.health.total_restarts = 0
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

    def _restart_engine(self, engineName) -> None:
        """Attempt engine restart with failure counting. Called from _jobThread."""
        if engineName not in self.engines:
            logging.error(f"_restart_engine: engine '{engineName}' not found")
            return
        engine = self.engines[engineName]
        engine.restart_count += 1
        if engine.restart_count <= TaskEngine.FAIL_LIMIT:
            logging.warning(f"Restarting engine '{engineName}' (attempt {engine.restart_count})")
            if engine.restart(self._default_pump):
                return  # success — engine stays in self.engines
            else:
                logging.critical(f"TaskEngine '{engineName}' restart failed, removing engine.")
        else:
            logging.critical(f"TaskEngine '{engineName}' exceeded restart limit ({TaskEngine.FAIL_LIMIT}), removing engine.")
        # Restart failed or limit exceeded — remove from engine pool
        del self.engines[engineName]

    def _runtime_limit_for(self, engine) -> float:
        """Seconds a job on this engine may run before it is treated as hung.

        Per-task `runtime_limit` in task_list overrides the `job_runtime_limit`
        health default. 0 (or absent) disables the check.
        """
        jobreq = getattr(engine, 'jobreq', None)
        if jobreq is not None:
            taskcfg = self.taskmenu.get(jobreq.jobTask) or {}
            limit = taskcfg.get('runtime_limit')
            if limit:
                return float(limit)
        return float(self._job_runtime_limit)

    def _check_engine_health(self, engine, engineName) -> None:
        """Evaluate engine health counters and trigger restart if thresholds exceeded.

        Covers the two failure modes that produce a COMPLETION event: a degraded
        accelerator (rate collapse) and outright errors. The third -- a job that
        never completes at all -- is the runtime deadline's, in the service loop.
        """
        # Consecutive degraded completions (accelerator rate collapse). The counter
        # keeps the name `consecutive_low_frames` because it is serialized into the
        # state file and rendered by the watchtower's sentinel page; what feeds it is
        # now the frame RATE, not the frame count.
        if engine.health.consecutive_low_frames >= self._strike_limit:
            if engine.health.total_restarts >= self._max_auto_restarts:
                logging.critical(f"Engine '{engineName}' exceeded lifetime auto-restart "
                                 f"limit ({self._max_auto_restarts}), no further restarts")
                engine.health.consecutive_low_frames = 0
                return
            if engine.health.last_restart is None or \
               (datetime.now() - engine.health.last_restart).total_seconds() > engine.health.restart_cooldown:
                logging.warning(f"Engine '{engineName}' health alert: "
                                f"{engine.health.consecutive_low_frames} consecutive low-frame "
                                f"completions, triggering restart")
                if engine.restart(self._default_pump):
                    engine.health.last_restart = datetime.now()
                    engine.health.total_restarts += 1
                engine.health.consecutive_low_frames = 0
                engine.health.consecutive_failures = 0
            return
        # Check consecutive outright failures
        if engine.health.consecutive_failures >= self._fail_strike_limit:
            if engine.health.total_restarts >= self._max_auto_restarts:
                logging.critical(f"Engine '{engineName}' exceeded lifetime auto-restart "
                                 f"limit ({self._max_auto_restarts}), no further restarts")
                engine.health.consecutive_failures = 0
                return
            if engine.health.last_restart is None or \
               (datetime.now() - engine.health.last_restart).total_seconds() > engine.health.restart_cooldown:
                logging.warning(f"Engine '{engineName}' health alert: "
                                f"{engine.health.consecutive_failures} consecutive failures, "
                                f"triggering restart")
                if engine.restart(self._default_pump):
                    engine.health.last_restart = datetime.now()
                    engine.health.total_restarts += 1
                engine.health.consecutive_failures = 0
                engine.health.consecutive_low_frames = 0

    def _build_status(self) -> dict:
        """Build a STATUS snapshot from JobManager thread context.

        Called safely within _jobThread — all state reads are single-threaded.
        Returns dict suitable for JSON serialization.
        """
        now = datetime.now()
        uptime = now - self._start_time
        engine_status = {}
        for name, engine in self.engines.items():
            # Compute per-engine aggregate stats from recent_jobs deque
            recent = list(engine.health.recent_jobs)
            completed = sum(1 for r in recent if r[3] == TaskEngine.TaskDONE)
            failed = sum(1 for r in recent if r[3] == TaskEngine.TaskFAIL)
            fps_vals = [r[2] for r in recent if r[2] > 0]
            elapsed_vals = [r[4].total_seconds() for r in recent if r[4] is not None]
            ring_start_vals = [r[5] for r in recent if len(r) > 5 and r[5] > 0]
            ring_next_vals = [r[6] for r in recent if len(r) > 6 and r[6] > 0]
            current_job = None
            status = "idle"
            cur_ring_start_avg, cur_ring_start_max, cur_ring_next_avg, cur_ring_next_max = engine.get_ring_latency()
            if engine.jobreq is not None:
                status = "running"
                current_job = engine.jobreq.jobTask
            engine_status[name] = {
                "alive": engine.is_alive(),
                "accelerator": engine.accelerator,
                "status": status,
                "job_count": engine.health.job_count,
                "current_task": current_job,
                "recent_completed": completed,
                "recent_failed": failed,
                "avg_fps": round(sum(fps_vals) / len(fps_vals), 2) if fps_vals else 0.0,
                "avg_elapsed_sec": round(sum(elapsed_vals) / len(elapsed_vals), 1) if elapsed_vals else 0.0,
                "health": {
                    "consecutive_low_frames": engine.health.consecutive_low_frames,
                    "consecutive_failures": engine.health.consecutive_failures,
                    "total_restarts": engine.health.total_restarts,
                    "ring_start_avg": round(sum(ring_start_vals) / len(ring_start_vals), 6) if ring_start_vals else 0.0,
                    "ring_start_max": round(max(ring_start_vals), 6) if ring_start_vals else 0.0,
                    "ring_next_avg": round(sum(ring_next_vals) / len(ring_next_vals), 6) if ring_next_vals else 0.0,
                    "ring_next_max": round(max(ring_next_vals), 6) if ring_next_vals else 0.0
                }
            }
        queue_info = {
            "queued": self._queue_depth,
            "running": self._running_jobs,
            "high_water_mark": self._queue_hwm,
            "latency_sec": round(self._queue_latency, 2),
            "latency_hwm_sec": round(self._queue_latency_hwm, 2)
        }
        # Engine utilization
        wall_clock = (now - self._start_time).total_seconds()
        utilization = {}
        for name in self.engines:
            busy = self._engine_busy_total.get(name, 0.0)
            if name in self._engine_busy_start:
                busy += (now - self._engine_busy_start[name]).total_seconds()
            utilization[name] = round((busy / wall_clock) * 100, 1) if wall_clock > 0 else 0.0
        # Submission rate
        cutoff = now - timedelta(seconds=self._submission_window)
        recent_submissions = sum(1 for t in self._submission_times if t >= cutoff)
        return {
            "flag": "HC",
            "component": "sentinel",
            "timestamp": now.isoformat(),
            "uptime": str(uptime).split('.')[0],  # trim microseconds
            "engines": engine_status,
            "queue": queue_info,
            "jobs_completed": self._jobs_completed,
            "jobs_failed": self._jobs_failed,
            # --- Queue diagnostics ---
            "queue_by_class": dict(self._queue_by_class),
            "queue_by_class_hwm": dict(self._queue_by_class_hwm),
            "utilization_pct": utilization,
            "submissions_5min": recent_submissions,
            "depth_hwm_snapshots": list(self._depth_hwm_snapshots),
            "latency_hwm_snapshots": list(self._latency_hwm_snapshots),
        }

    def _capture_queue_snapshot(self, trigger) -> dict:
        """Capture a lightweight snapshot of queue and engine state.

        Called from _jobThread when a queue HWM is broken. All state reads
        are safe — we're in the JobManager thread context.
        """
        now = datetime.now()
        engine_snapshot = {}
        for name, engine in self.engines.items():
            if engine.jobreq is not None:
                jreq = engine.jobreq
                elapsed = (now - jreq.jobStartTime).total_seconds() if jreq.jobStartTime else 0
                engine_snapshot[name] = {
                    "task": jreq.jobTask,
                    "class": jreq.jobClass,
                    "event": jreq.eventID[:8] if jreq.eventID else None,
                    "elapsed_sec": round(elapsed, 1),
                    "status": "running",
                    "images": engine.get_image_cnt(),
                    "rate": engine.get_image_rate(),
                }
            else:
                engine_snapshot[name] = {"task": None, "status": "idle"}
        with jobLock:
            queued = [r for r in taskList.values() if r.jobStatus == JobRequest.Status_QUEUED]
        task_breakdown = {}
        for r in queued:
            task_breakdown[r.jobTask] = task_breakdown.get(r.jobTask, 0) + 1
        cutoff = now - timedelta(seconds=self._submission_window)
        recent_submissions = sum(1 for t in self._submission_times if t >= cutoff)
        return {
            "trigger": trigger,
            "timestamp": now.isoformat(),
            "queue_depth": self._queue_depth,
            "queue_latency_sec": round(self._queue_latency, 1),
            "by_class": dict(self._queue_by_class),
            "by_task": task_breakdown,
            "engines": engine_snapshot,
            "submissions_5min": recent_submissions,
        }

    def _releaseJob(self, jobid, engine) -> None:
        logging.debug(f"Release job {jobid}")
        jreq = taskList[jobid]
        jreq.registerJOB(engine)
        self.engines[engine].dataFeed = self._setPump(jreq.datapump)
        if jreq.eventID:
            jreq.camsize = self._getFrameDimensons(jreq)
        if not self.engines[engine].start_job(jreq):
            jreq.deregisterJOB(TaskEngine.TaskFAIL, (0,0))
            self.ondeck[engine] = None

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
            taskEngine.ringBuffer.put(simplejpeg.decode_jpeg(jpeg, colorspace='BGR'))
            #taskEngine.ringBuffer.put(cv2.imdecode(np.frombuffer(jpeg, dtype='uint8'), -1))
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

    # --- State snapshot helpers (all run in _jobThread context) ---

    def _snapshot_job_records(self) -> dict:
        """Package taskList into plain dicts for serialization."""
        with jobLock:
            return {jobid: jobreq.to_dict() for jobid, jobreq in taskList.items()}

    def _snapshot_queue_records(self) -> list:
        """Package queued and on-deck jobs for serialization."""
        records = []
        # On-deck jobs
        for jreq in self.ondeck.values():
            if jreq is not None:
                records.append({
                    'jobid': jreq.jobID,
                    'event': jreq.eventID,
                    'task': jreq.jobTask,
                    'priority': jreq.priority,
                    'sink': jreq.dataSink,
                    'node': jreq.sourceNode,
                    'date': jreq.eventDate,
                    'pump': jreq.datapump,
                })
        # Queued jobs not already captured as on-deck
        ondeck_ids = {r['jobid'] for r in records}
        with jobLock:
            for jreq in taskList.values():
                if (jreq.jobStatus == JobRequest.Status_QUEUED
                        and jreq.jobID not in ondeck_ids):
                    records.append({
                        'jobid': jreq.jobID,
                        'event': jreq.eventID,
                        'task': jreq.jobTask,
                        'priority': jreq.priority,
                        'sink': jreq.dataSink,
                        'node': jreq.sourceNode,
                        'date': jreq.eventDate,
                        'pump': jreq.datapump,
                    })
        # Running jobs (for checkpoint — they restart from scratch on recovery)
        for engine in self.engines.values():
            jreq = engine.jobreq
            if jreq is not None and jreq.jobID not in ondeck_ids:
                records.append({
                    'jobid': jreq.jobID,
                    'event': jreq.eventID,
                    'task': jreq.jobTask,
                    'priority': jreq.priority,
                    'sink': jreq.dataSink,
                    'node': jreq.sourceNode,
                    'date': jreq.eventDate,
                    'pump': jreq.datapump,
                })
        return records

    def _snapshot_engine_health(self) -> dict:
        """Package engine health counters for serialization."""
        result = {}
        for name, engine in self.engines.items():
            h = engine.health
            result[name] = {
                'total_restarts': h.total_restarts,
                'consecutive_low_frames': h.consecutive_low_frames,
                'consecutive_failures': h.consecutive_failures,
                'last_restart': h.last_restart.isoformat() if h.last_restart else None,
                'job_count': h.job_count,
            }
        return result

    def _snapshot_diagnostics(self) -> dict:
        """Package period accumulator values for serialization."""
        now = datetime.now()
        cutoff = now - timedelta(seconds=self._submission_window)
        recent_submissions = sum(1 for t in self._submission_times if t >= cutoff)
        return {
            'start_time': self._start_time.isoformat(),
            'engine_busy_total': dict(self._engine_busy_total),
            'queue_hwm': self._queue_hwm,
            'queue_latency_hwm': round(self._queue_latency_hwm, 2),
            'queue_by_class_hwm': dict(self._queue_by_class_hwm),
            'submissions_peak_5min': recent_submissions,
            'depth_hwm_snapshot_count': len(self._depth_hwm_snapshots),
            'latency_hwm_snapshot_count': len(self._latency_hwm_snapshots),
        }

    def _run_maintenance(self) -> dict:
        """Execute daily maintenance. Called from _jobThread via snapshot mechanism."""
        now = datetime.now()
        logging.info("Maintenance started")

        # Step 1: Compute daily summaries from taskList
        date_summaries = {}
        processed_dates = []
        with jobLock:
            all_jobs = list(taskList.values())
        for jreq in all_jobs:
            d = jreq.eventDate or 'unknown'
            if d not in date_summaries:
                date_summaries[d] = []
            date_summaries[d].append(jreq)

        # Daily maintenance is scheduled just after midnight, so summarize
        # the prior day rather than the new calendar day that has just begun.
        target_date_key = (now - timedelta(days=1)).strftime('%Y-%m-%d')

        for date_key, jobs in date_summaries.items():
            # Only emit HEALTH records for the maintenance target date.
            # Older dates already have their definitive record from prior
            # maintenance runs, and the new day is still incomplete.
            if date_key != target_date_key:
                continue
            processed_dates.append(date_key)
            # Per-engine rollup
            engine_stats = {}
            for jreq in jobs:
                eng = jreq.engine or 'unassigned'
                if eng not in engine_stats:
                    engine_stats[eng] = {
                        'jobs': 0, 'failed': 0, 'restarts': 0,
                        'rates': [], 'elapsed_vals': [],
                        'ring_start_vals': [], 'ring_next_vals': [],
                    }
                es = engine_stats[eng]
                es['jobs'] += 1
                if jreq.jobStatus == JobRequest.Status_FAILED:
                    es['failed'] += 1
                if jreq.image_rate > 0:
                    es['rates'].append(jreq.image_rate)
                if jreq.jobStartTime and jreq.jobEndTime:
                    es['elapsed_vals'].append(
                        (jreq.jobEndTime - jreq.jobStartTime).total_seconds())
                if jreq.ring_start_avg > 0:
                    es['ring_start_vals'].append(jreq.ring_start_avg)
                if jreq.ring_next_avg > 0:
                    es['ring_next_vals'].append(jreq.ring_next_avg)

            # Finalize engine rollup
            engines_summary = {}
            for eng, es in engine_stats.items():
                entry = {
                    'jobs': es['jobs'],
                    'failed': es['failed'],
                }
                if es['rates']:
                    entry['rate_mean'] = round(sum(es['rates']) / len(es['rates']), 2)
                if es['elapsed_vals']:
                    entry['elapsed_mean'] = round(
                        sum(es['elapsed_vals']) / len(es['elapsed_vals']), 1)
                if es['ring_start_vals']:
                    entry['ring_start_avg'] = round(
                        sum(es['ring_start_vals']) / len(es['ring_start_vals']), 6)
                if es['ring_next_vals']:
                    entry['ring_next_avg'] = round(
                        sum(es['ring_next_vals']) / len(es['ring_next_vals']), 6)
                # Add utilization for real engines
                if eng in self.engines:
                    wall_clock = (now - self._start_time).total_seconds()
                    busy = self._engine_busy_total.get(eng, 0.0)
                    if eng in self._engine_busy_start:
                        busy += (now - self._engine_busy_start[eng]).total_seconds()
                    entry['utilization_pct'] = round(
                        (busy / wall_clock) * 100, 1) if wall_clock > 0 else 0.0
                    entry['restarts'] = self.engines[eng].health.total_restarts
                engines_summary[eng] = entry

            # Per-task rollup
            task_stats = {}
            for jreq in jobs:
                t = jreq.jobTask or 'unknown'
                if t not in task_stats:
                    task_stats[t] = {'jobs': 0, 'failed': 0, 'elapsed_vals': []}
                ts = task_stats[t]
                ts['jobs'] += 1
                if jreq.jobStatus == JobRequest.Status_FAILED:
                    ts['failed'] += 1
                if jreq.jobStartTime and jreq.jobEndTime:
                    ts['elapsed_vals'].append(
                        (jreq.jobEndTime - jreq.jobStartTime).total_seconds())
            tasks_summary = {}
            for t, ts in task_stats.items():
                entry = {'jobs': ts['jobs'], 'failed': ts['failed']}
                if ts['elapsed_vals']:
                    entry['elapsed_mean'] = round(
                        sum(ts['elapsed_vals']) / len(ts['elapsed_vals']), 1)
                tasks_summary[t] = entry

            # System totals
            total_jobs = len(jobs)
            total_failed = sum(1 for j in jobs if j.jobStatus == JobRequest.Status_FAILED)

            # Step 2: Publish daily summary as HEALTH record
            system_stats = {
                'total_jobs': total_jobs,
                'total_failed': total_failed,
            }
            cutoff = now - timedelta(seconds=self._submission_window)
            recent_submissions = sum(1 for t in self._submission_times if t >= cutoff)
            system_stats.update({
                'queue_hwm': self._queue_hwm,
                'queue_by_class_hwm': dict(self._queue_by_class_hwm),
                'queue_latency_hwm_sec': round(self._queue_latency_hwm, 1),
                'submissions_peak_5min': recent_submissions,
                'depth_hwm_snapshot_count': len(self._depth_hwm_snapshots),
                'latency_hwm_snapshot_count': len(self._latency_hwm_snapshots),
            })
            health_record = json.dumps({
                'flag': 'HEALTH',
                'type': 'daily_summary',
                'date': date_key,
                'engines': engines_summary,
                'tasks': tasks_summary,
                'system': system_stats,
            })
            logging.info(health_record)

        # Step 3: Reset HWM and utilization accumulators
        self._queue_hwm = self._queue_depth
        self._queue_latency_hwm = self._queue_latency
        self._queue_by_class_hwm = dict(self._queue_by_class)
        self._depth_hwm_snapshots.clear()
        self._latency_hwm_snapshots.clear()
        for name in self._engine_busy_total:
            self._engine_busy_total[name] = 0.0
        self._engine_busy_start.clear()

        # Step 4: Checkpoint
        save_state(self._state_filepath,
                   now, now - self._start_time,
                   self._snapshot_job_records(),
                   self._snapshot_queue_records(),
                   self._snapshot_engine_health(),
                   self._snapshot_diagnostics())

        # Step 5: Trim completed records older than retention period
        trim_cutoff = now - timedelta(hours=self._retention_hours)
        trimmed = 0
        terminal_statuses = {
            JobRequest.Status_DONE,
            JobRequest.Status_FAILED,
            JobRequest.Status_CHAINED,
            JobRequest.Status_CANCELED,
        }
        with jobLock:
            to_remove = [jobid for jobid, jreq in taskList.items()
                         if jreq.jobStatus in terminal_statuses
                         and jreq.jobSubmitTime < trim_cutoff]
            for jobid in to_remove:
                del taskList[jobid]
                trimmed += 1

        # Step 6: Return summary
        result = {
            'status': 'OK',
            'dates_processed': processed_dates,
            'target_date': target_date_key,
            'total_jobs_reviewed': len(all_jobs),
            'trimmed_records': trimmed,
            'remaining_records': len(taskList),
        }
        logging.info(f"Maintenance complete: {result}")
        return result

    def _run_shutdown(self) -> dict:
        """Execute graceful shutdown sequence. Called from _jobThread via snapshot mechanism.

        Sets draining flag, waits for running tasks to complete (with timeout),
        serializes state, and signals the main loop to exit.
        """
        logging.warning("Graceful shutdown initiated")

        # Step 1: Set draining flag — stops new job ventilation
        self._draining.set()

        # Step 2: Wait for running engines to go idle
        deadline = time.monotonic() + self._drain_timeout
        engines_drained = []
        engines_timed_out = []
        while time.monotonic() < deadline:
            all_idle = True
            for engineName, engine in self.engines.items():
                if engine.getJobID() is not None:
                    all_idle = False
                    # Continue servicing ring buffers so running tasks can complete
                    if engine.is_alive() and engine.have_request():
                        (cmd, key) = engine.get_request()
                        if cmd == JobManager.ReadSTART:
                            self._feedStart(engine, key)
                            engine.send_response(engine.ringBuffer.get())
                        elif cmd == JobManager.ReadNEXT:
                            engine.ringBuffer.frame_complete()
                            engine.send_response(engine.ringBuffer.get())
                    if engine.cursor:
                        self._feedNext(engine)
            # Also drain taskFeed so completions get processed
            while not taskFeed.empty():
                try:
                    (tag, msg) = taskFeed.get()
                    if tag in [TaskEngine.TaskDONE, TaskEngine.TaskFAIL,
                               TaskEngine.TaskCANCELED, TaskEngine.TaskCHAIN]:
                        if tag == TaskEngine.TaskCHAIN:
                            # During drain, don't chain — mark as done
                            (jobid, _task) = msg
                            jreq = taskList.get(jobid)
                            if jreq:
                                eng = self.engines.get(jreq.engine)
                                if eng:
                                    task_stats = (eng.get_image_cnt(), eng.get_image_rate())
                                    jreq.deregisterJOB(TaskEngine.TaskDONE, task_stats)
                                    eng.jobreq = None
                        else:
                            jreq = taskList.get(msg)
                            if jreq:
                                eng = self.engines.get(jreq.engine)
                                if eng and eng.jobreq and eng.jobreq.jobID == msg:
                                    task_stats = (eng.get_image_cnt(), eng.get_image_rate())
                                    jreq.deregisterJOB(tag, task_stats)
                                    eng.jobreq = None
                    taskFeed.task_done()
                except Exception:
                    logging.exception("Exception draining taskFeed during shutdown")
            if all_idle:
                break
            time.sleep(0.02)

        # Classify engines
        for engineName, engine in self.engines.items():
            if engine.getJobID() is None:
                engines_drained.append(engineName)
            else:
                engines_timed_out.append(engineName)
                # Reset still-running jobs to Queued so they re-execute on recovery
                jreq = engine.getJobRequest()
                if jreq:
                    logging.warning(f"Engine '{engineName}' timed out during drain, "
                                    f"resetting job {jreq.jobID} to Queued")
                    jreq.jobStatus = JobRequest.Status_QUEUED
                    jreq.jobStartTime = None
                    jreq.engine = None

        logging.warning(f"Drain complete: {len(engines_drained)} drained, "
                        f"{len(engines_timed_out)} timed out: {engines_timed_out}")

        # Step 3: Serialize state
        now = datetime.now()
        save_state(self._state_filepath,
                   now, now - self._start_time,
                   self._snapshot_job_records(),
                   self._snapshot_queue_records(),
                   self._snapshot_engine_health(),
                   self._snapshot_diagnostics())
        logging.warning("State checkpoint saved for shutdown")

        # Step 4: Terminate TaskEngine children
        for engineName, engine in list(self.engines.items()):
            try:
                engine._engine.terminate()
                logging.info(f"Engine '{engineName}' terminated")
            except Exception:
                logging.exception(f"Error terminating engine '{engineName}'")

        # Step 5: Signal the main loop to exit
        self._stop = True

        return {
            'status': 'SHUTDOWN',
            'engines_drained': engines_drained,
            'engines_timed_out': engines_timed_out,
            'state_file': self._state_filepath,
        }

    def _jobThread(self) -> None:
        _diag_interval = 50
        _diag_counter = 0
        # Classify any recovered jobs that were saved before TaskSUBMIT processing
        with jobLock:
            for jobreq in taskList.values():
                if jobreq.jobStatus == JobRequest.Status_QUEUED and jobreq.jobClass is None:
                    if jobreq.jobTask in self.taskmenu:
                        jobreq.jobClass = self.taskmenu[jobreq.jobTask]['class']
                        logging.debug(f"Classified recovered job {jobreq.jobID} as class {jobreq.jobClass}")
                    else:
                        logging.warning(f"Cannot classify recovered job {jobreq.jobID}: unknown task '{jobreq.jobTask}'")
        logging.info("Job Manager thread ready")
        while not self._stop:
            if not taskFeed.empty():
                try:
                    (tag, msg) = taskFeed.get()
                    # Have a task start request or job status update
                    tag_name = JobRequest.Status[tag] if 0 <= tag < len(JobRequest.Status) else f"Tag({tag})"
                    logging.debug(f"Job Manager has queue entry {(tag_name, msg)}")

                    if tag == JobManager.JobSNAPSHOT:
                        # Thread-safe snapshot request
                        snapshot_req = msg
                        if snapshot_req.kind == 'STATUS':
                            snapshot_req.result = self._build_status()
                        elif snapshot_req.kind == 'HISTORY':
                            JobRequest.full_history_report()
                            snapshot_req.result = True
                        elif snapshot_req.kind == 'MAINTENANCE':
                            snapshot_req.result = self._run_maintenance()
                        elif snapshot_req.kind == 'SHUTDOWN':
                            snapshot_req.result = self._run_shutdown()
                        snapshot_req.event.set()

                    elif tag == TaskEngine.TaskSUBMIT:
                        # New task request received — classify and try fair ondeck placement
                        jobreq = taskList[msg]
                        jobreq.jobClass = self.taskmenu[jobreq.jobTask]['class']
                        self._submission_times.append(datetime.now())
                        # Fast path: if an ondeck slot is available, place the OLDEST
                        # queued job of this class (not necessarily the new one). This
                        # keeps responsiveness while ensuring FIFO fairness.
                        for engineName, engine in self.engines.items():
                            if jobreq.jobClass in engine.getClasses():
                                if self.ondeck[engineName] is None:
                                    already_ondeck = {j.jobID for j in self.ondeck.values() if j is not None}
                                    with jobLock:
                                        candidates = [r for r in taskList.values()
                                            if r.jobStatus == JobRequest.Status_QUEUED
                                            and r.jobID not in already_ondeck
                                            and r.jobClass == jobreq.jobClass]
                                    if candidates:
                                        oldest = min(candidates, key=lambda r: r.jobSubmitTime)
                                        self.ondeck[engineName] = oldest
                                    break

                    elif tag == TaskEngine.TaskSTARTED:
                        # Task start confirmed
                        jobreq = taskList[msg]
                        if jobreq.engine in self.ondeck:
                            self.ondeck[jobreq.engine] = None
                        self._engine_busy_start[jobreq.engine] = datetime.now()

                    elif tag == TaskEngine.TaskCHAIN:
                        # Handle task chaining request
                        (jobid, task) = msg
                        self._chainJob(jobid, task)

                    elif tag in [TaskEngine.TaskDONE, TaskEngine.TaskFAIL, TaskEngine.TaskCANCELED]:
                        # Task completed, failed or was canceled
                        jobreq = taskList[msg]
                        engine = self.engines.get(jobreq.engine)
                        if engine and engine.jobreq and engine.jobreq.jobID == msg:
                            engine.jobreq = None
                            # Accumulate engine busy time
                            busy_start = self._engine_busy_start.pop(jobreq.engine, None)
                            if busy_start:
                                self._engine_busy_total[jobreq.engine] = \
                                    self._engine_busy_total.get(jobreq.engine, 0.0) + \
                                    (datetime.now() - busy_start).total_seconds()
                            task_stats = (engine.get_image_cnt(), engine.get_image_rate())
                            ring_start_avg, ring_start_max, ring_next_avg, ring_next_max = engine.get_ring_latency()
                            jobreq.ring_start_avg = ring_start_avg
                            jobreq.ring_next_avg = ring_next_avg
                            jobreq.deregisterJOB(tag, task_stats)
                            if tag == TaskEngine.TaskDONE:
                                engine.restart_count = 0  # reset on successful completion
                            logging.debug(f"Engine {engine.getName()} gone idle.")
                            # Update engine health counters
                            engineName = engine.getName()
                            elapsed = jobreq.jobEndTime - jobreq.jobStartTime if jobreq.jobStartTime else None
                            engine.health.job_count += 1
                            engine.health.recent_jobs.append(
                                (jobreq.jobTask, task_stats[0], task_stats[1], tag, elapsed, ring_start_avg, ring_next_avg))
                            if tag == TaskEngine.TaskFAIL:
                                engine.health.consecutive_failures += 1
                            else:
                                engine.health.consecutive_failures = 0
                            # Degradation is a RATE collapse, not a short job. This
                            # previously tested the frame COUNT, which a legitimate
                            # short event trips: a person in view with no faces for
                            # the duration yields a handful of frames at a perfectly
                            # healthy rate (9% of GetFaces jobs land at <=5 frames),
                            # and three in a row restarted a working engine. Only the
                            # interleaved high-count MobileNetSSD jobs kept resetting
                            # the counter -- accidental protection, not a rule.
                            # Judge on the rate, on a COMPLETED job with enough frames
                            # to measure. The other two failure modes have their own
                            # detectors: a job that never completes is the runtime
                            # deadline's, and outright errors are consecutive_failures'.
                            if (engine.accelerator == 'coral'
                                    and tag == TaskEngine.TaskDONE
                                    and task_stats[0] >= self._min_rate_frames
                                    and task_stats[1] <= self._low_rate_threshold):
                                engine.health.consecutive_low_frames += 1
                            else:
                                engine.health.consecutive_low_frames = 0
                            self._check_engine_health(engine, engineName)
                            # Update completion counters
                            if tag == TaskEngine.TaskDONE:
                                self._jobs_completed += 1
                                # Incremental checkpoint
                                self._checkpoint_counter += 1
                                if self._checkpoint_counter >= self._checkpoint_interval:
                                    save_state(self._state_filepath,
                                               datetime.now(),
                                               datetime.now() - self._start_time,
                                               self._snapshot_job_records(),
                                               self._snapshot_queue_records(),
                                               self._snapshot_engine_health(),
                                               self._snapshot_diagnostics())
                                    self._checkpoint_counter = 0
                            elif tag == TaskEngine.TaskFAIL:
                                self._jobs_failed += 1
                        if jobreq.engine in self.ondeck and self.ondeck[jobreq.engine] == jobreq:
                            self.ondeck[jobreq.engine] = None

                    elif tag == TaskEngine.TaskBOMB:
                        # Handle engine failure — attempt restart
                        logging.critical(f"TaskEngine '{msg}' failed catastrophically — attempting restart.")
                        self._restart_engine(msg)

                    elif tag == TaskEngine.TaskRESTART:
                        # Manual restart request
                        self._restart_engine(msg)

                    else:
                        logging.error(f"Undefined status '{tag}' for job {msg}")
                except TimeoutError as e:
                    logging.error(f"JobManager timeout {str(e)}")
                except Exception:
                    logging.exception("JobManager unexpected exception")
                taskFeed.task_done()
                logging.debug(f"Now ondeck {str(self._ondeck_status())}")

            # Service the ring buffers for running tasks.
            runningTasks = 0
            dead_engines = []
            overrun_engines = []
            for engineName in list(self.engines):
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
                        # A job that never ends. The Coral EdgeTPU's invoke() is an
                        # uninterruptible blocking call into libedgetpu: once the
                        # device wedges, the child sits inside C where no signal
                        # handler runs and no stop flag is read, the job never
                        # completes, and the queue backs up behind it until someone
                        # notices. Every other health check keys off a COMPLETION
                        # event, so this is the one failure none of them can see.
                        limit = self._runtime_limit_for(engine)
                        task_start = getattr(engine, 'task_start', None)
                        if limit and task_start is not None:
                            ran = time.monotonic() - task_start
                            if ran > limit:
                                jobreq = getattr(engine, 'jobreq', None)
                                overrun_engines.append(
                                    (engineName, jobreq.jobTask if jobreq else '?', ran, limit))
                else:
                    dead_engines.append(engineName)
            for engineName in dead_engines:
                if self._stop or self._draining.is_set():
                    break
                logging.error(f"TaskEngine '{engineName}' found dead, attempting restart.")
                self._restart_engine(engineName)

            for engineName, taskName, ran, limit in overrun_engines:
                if self._stop or self._draining.is_set():
                    break
                engine = self.engines.get(engineName)
                if engine is None:
                    continue
                if engine.health.total_restarts >= self._max_auto_restarts:
                    logging.critical(
                        f"TaskEngine '{engineName}' job '{taskName}' hung "
                        f"({ran:.0f}s > {limit:.0f}s) but the lifetime auto-restart "
                        f"limit ({self._max_auto_restarts}) is exhausted — engine left as is")
                    continue
                logging.critical(
                    f"TaskEngine '{engineName}' job '{taskName}' exceeded its runtime limit "
                    f"({ran:.0f}s > {limit:.0f}s) — restarting. A blocked accelerator call "
                    f"cannot be interrupted in-process; killing the child is the only exit.")
                # restart() fails the in-flight job, kills the child, resets the ring
                # buffers and re-forks with a full handshake.
                if engine.restart(self._default_pump):
                    engine.health.last_restart = datetime.now()
                    engine.health.total_restarts += 1
                    engine.health.consecutive_low_frames = 0
                    engine.health.consecutive_failures = 0
                else:
                    logging.critical(f"TaskEngine '{engineName}' restart failed after hang")
                    self._restart_engine(engineName)

            # Assign jobs ondeck to available engines
            if runningTasks < len(self.engines):
                for engineName, engine in self.engines.items():
                    if engine.getJobID() is None:
                        jreq = self.ondeck.get(engineName)
                        if jreq is not None:
                            logging.debug(f"Found on deck for engine {engineName}: {jreq.jobID}")
                            self._releaseJob(jreq.jobID, engineName)

                # Ventilate queued jobs to empty per-engine ondeck slots by priority
                if not self._draining.is_set():
                    for engineName, engine in self.engines.items():
                        if self.ondeck[engineName] is None:
                            already_ondeck = {j.jobID for j in self.ondeck.values() if j is not None}
                            with jobLock:
                                pending = []
                                # Try each priority level in order
                                for priority in [1, 2, 3]:
                                    pending = [r for r in taskList.values()
                                        if r.jobStatus == JobRequest.Status_QUEUED
                                        and r.jobID not in already_ondeck
                                        and r.jobClass in engine.getClasses()
                                        and r.priority == priority]
                                    if pending:
                                        break
                            if pending:
                                jobreq = pending[0]
                                self.ondeck[engineName] = jobreq
                                logging.debug(f"Ventilating job {jobreq.jobID} to ondeck slot for engine {engineName}")

            self._running_jobs = runningTasks
            _diag_counter += 1
            if _diag_counter >= _diag_interval:
                _diag_counter = 0
                with jobLock:
                    queued_jobs = [r for r in taskList.values() if r.jobStatus == JobRequest.Status_QUEUED]
                    self._queue_depth = len(queued_jobs)
                    # Per-class breakdown (skip unclassified — transient pre-submit state)
                    class_counts = {}
                    for r in queued_jobs:
                        if r.jobClass is not None:
                            class_counts[r.jobClass] = class_counts.get(r.jobClass, 0) + 1
                    self._queue_by_class = class_counts
                    for c, n in class_counts.items():
                        if n > self._queue_by_class_hwm.get(c, 0):
                            self._queue_by_class_hwm[c] = n
                    # Latency of oldest queued job
                    self._queue_latency = (datetime.now() - min((r.jobSubmitTime for r in queued_jobs),
                        default=datetime.now())).total_seconds() if self._queue_depth > 0 else 0.0
                # HWM snapshot capture
                if self._queue_depth > self._queue_hwm:
                    self._queue_hwm = self._queue_depth
                    self._depth_hwm_snapshots.append(self._capture_queue_snapshot("depth"))
                else:
                    self._queue_hwm = max(self._queue_hwm, self._queue_depth)
                if self._queue_latency > self._queue_latency_hwm:
                    self._queue_latency_hwm = self._queue_latency
                    self._latency_hwm_snapshots.append(self._capture_queue_snapshot("latency"))
                else:
                    self._queue_latency_hwm = max(self._queue_latency_hwm, self._queue_latency)
                # Trim submission rate window
                cutoff = datetime.now() - timedelta(seconds=self._submission_window)
                while self._submission_times and self._submission_times[0] < cutoff:
                    self._submission_times.popleft()

            if taskFeed.empty() and runningTasks == 0:
                time.sleep(0.05)

    def close(self):
        self._stop = True
        self._thread.join()

async def task_loop(asyncREP, taskCFG):
    logging.info("Sentinel control loop running")
    while True:
        reply = 'OK'
        reply_sent = False
        msg = await asyncREP.recv()
        payload = msg.decode("ascii")
        try:
            request = json.loads(payload)
            if 'task' in request:
                task = request['task']
                if task == 'HISTORY':
                    snapshot = SnapshotRequest('HISTORY')
                    taskFeed.put((JobManager.JobSNAPSHOT, snapshot))
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, snapshot.event.wait, 5.0)
                elif task == 'STATUS':
                    snapshot = SnapshotRequest('STATUS')
                    taskFeed.put((JobManager.JobSNAPSHOT, snapshot))
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, snapshot.event.wait, 5.0)
                    if snapshot.result:
                        logging.info(json.dumps(snapshot.result))
                        reply = json.dumps(snapshot.result)
                    else:
                        reply = 'Error'
                elif task == 'ALERT':
                    # Re-broadcast alert payload as lifecycle event for all subscribers
                    if 'payload' in request:
                        logging.info(json.dumps(request['payload']))
                    else:
                        logging.error(f"ALERT request missing payload: {request}")
                        reply = 'Error'
                elif task == 'HEALTH_REPORT':
                    # Re-broadcast health report for all subscribers (same pattern as ALERT)
                    if 'payload' in request:
                        logging.info(json.dumps(request['payload']))
                    else:
                        logging.error(f"HEALTH_REPORT request missing payload: {request}")
                        reply = 'Error'
                elif task == 'MAINTENANCE':
                    snapshot = SnapshotRequest('MAINTENANCE')
                    taskFeed.put((JobManager.JobSNAPSHOT, snapshot))
                    loop = asyncio.get_event_loop()
                    timed_out = not await loop.run_in_executor(
                        None, snapshot.event.wait, 30.0)
                    if timed_out:
                        logging.error("MAINTENANCE request timed out")
                        reply = 'Error'
                    elif snapshot.result:
                        reply = json.dumps(snapshot.result)
                    else:
                        reply = 'Error'
                elif task == 'RESTART_ENGINE':
                    if 'engine' in request:
                        taskFeed.put((TaskEngine.TaskRESTART, request['engine']))
                        reply = f"Restart requested for engine '{request['engine']}'"
                    else:
                        logging.error(f"RESTART_ENGINE request missing engine name: {request}")
                        reply = 'Error'
                elif task == 'SHUTDOWN':
                    await asyncREP.send(b'DRAINING')
                    reply_sent = True
                    snapshot = SnapshotRequest('SHUTDOWN')
                    taskFeed.put((JobManager.JobSNAPSHOT, snapshot))
                    _scfg = CFG.get('shutdown', {})
                    _timeout = _scfg.get('drain_timeout', 60) + 15
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, snapshot.event.wait, _timeout)
                    if snapshot.result:
                        logging.warning(f"Shutdown complete: {json.dumps(snapshot.result)}")
                    else:
                        logging.error("SHUTDOWN request timed out")
                    shutdown_event.set()
                    return  # Exit task_loop coroutine
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
            logging.exception("Unexpected exception processing task request")
            reply = 'Error'
        finally:
            if not reply_sent:
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
                taskFeed.put((msgTag, msg[0].strip()))
                logging.critical(f"TaskEngine {taskMsg} failure.")
            else:
                logging.error(f"Unsupported task message: {msgTag}")

async def _shutdown_waiter():
    """Coroutine that waits for the shutdown_event and raises SystemExit."""
    await shutdown_event.wait()
    raise SystemExit('Graceful shutdown')

async def main():
    global shutdown_event
    log = start_logging(CFG["logging_port"])
    shutdown_event = asyncio.Event()

    # SIGTERM handler — initiates graceful shutdown via the control path
    def handle_sigterm():
        log.warning("SIGTERM received, initiating graceful shutdown")
        # Synthesize a SHUTDOWN command by putting it directly on taskFeed
        snapshot = SnapshotRequest('SHUTDOWN')
        taskFeed.put((JobManager.JobSNAPSHOT, snapshot))
        # After _run_shutdown completes in _jobThread, it sets _stop.
        # We need a way for the async loop to notice. Use a thread to
        # wait on the snapshot event and then set the asyncio shutdown_event.
        def _wait_and_signal():
            _scfg = CFG.get('shutdown', {})
            snapshot.event.wait(timeout=_scfg.get('drain_timeout', 60) + 15)
            loop.call_soon_threadsafe(shutdown_event.set)
        threading.Thread(target=_wait_and_signal, daemon=True).start()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, handle_sigterm)

    # State recovery — before creating engines or entering event loop
    _mcfg = CFG.get('maintenance', {})
    state_filepath = os.path.expanduser(
        _mcfg.get('state_file', '~/sentinel/state.json'))
    recovered = load_state(state_filepath)
    recovered_health = None
    if recovered:
        # Seed taskList with historical records (history-only recovery)
        for jobid, fields in recovered.get('jobs', {}).items():
            taskList[jobid] = JobRequest.from_dict(jobid, fields)
        recovered_health = recovered.get('engine_health')
        log.info(f"Restored {len(recovered.get('jobs', {}))} history records")

    asyncREP = ctxAsync.socket(zmq.REP)  # task loop control socket
    asyncSUB = ctxAsync.socket(zmq.SUB)  # subscriptions for job result publishers
    asyncREP.bind(f"tcp://*:{CFG['control_port']}")
    asyncSUB.subscribe(b'')
    manager = JobManager(CFG["task_engines"],
                         CFG["ring_buffer_models"],
                         CFG["task_list"],
                         CFG["default_pump"],
                         asyncSUB,
                         recovered_health=recovered_health)
    try:
        log.info("Sentinel started")
        await asyncio.gather(task_loop(asyncREP, CFG["task_list"]),
                             task_feedback(asyncSUB),
                             _shutdown_waiter())
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
