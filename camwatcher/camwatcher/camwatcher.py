"""camwatcher: A component of the SentinelCam data layer.
Proivides subscriber services for log and image publishing from
outpost nodes. Drives a dispatcher to trigger other functionality.

Copyright (c) 2021 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import asyncio
import glob
import json
import logging
import logging.config
import multiprocessing
import shutil
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
from sentinelcam.utils import ImageSubscriber, CropSubscriber, readConfig

CFG = readConfig(os.path.join(os.path.expanduser("~"), "camwatcher.yaml"))

outposts = {}                        # outpost image subscribers by (node,view)
crop_writers = {}                    # OAK crop subscribers by (node,view); only crop-publishing nodes
outpost_health = {}                  # per-outpost health tracking by node name
_event_meta = {}                     # (node,view,event) -> (camsize, start_ts), cached from the trk 'start'
_crp_active = set()                  # (node,view,event) with an open crp correlation file
_crp_record_counts = {}              # (node,view) -> total crp records captured; HC reconciliation vs crops written
threadLock = threading.Lock()        # coordinate updates to list of outpost subscribers
dbLogMsgQ = queue.Queue()            # log data content messages for CSV writer
dateIndxQ = multiprocessing.Queue()  # for creating new camwatcher index entries
sentinel_alertQ = multiprocessing.Queue()  # for sending alerts to sentinel

_start_time = datetime.now()         # process start time for uptime tracking
_events_today = multiprocessing.Value('L', 0)  # event counter for current date
_sentinel_agent = None               # SentinelAgent instance set in main()

class OutpostHealth:
    """Tracks heartbeat health for an individual outpost node."""
    def __init__(self, node_name, expected_interval=300):
        self.node = node_name
        self.expected_interval = expected_interval  # heartbeat every 5 min
        self.last_heartbeat = None      # datetime
        self.last_fps = None
        self.missed_heartbeats = 0      # consecutive missed
        self.events_today = 0

    def record_heartbeat(self, fps=None):
        self.last_heartbeat = datetime.now()
        self.last_fps = fps
        self.missed_heartbeats = 0

    def age_seconds(self):
        if self.last_heartbeat is None:
            return None
        return (datetime.now() - self.last_heartbeat).total_seconds()

# multiprocessing class implementing a subprocess image writer
class ImageStreamWriter:

    def __init__(self, node_view, publisher, imagedir):
        self.node_view = node_view
        self._writeImages = multiprocessing.Value('i', 0)
        self._frames_written = multiprocessing.Value('L', 0)  # monotonic frame counter for HC
        self._eventQueue = multiprocessing.Queue()
        self.process = multiprocessing.Process(target=self._image_subscriber, args=(
            self._writeImages, self._frames_written, self._eventQueue, publisher, node_view[1], imagedir))
        self.process.start()
        logging.debug(f"ImageStreamWriter started for {node_view} pid {self.process.pid} in {imagedir}")

    def _set_datedir(self, dir, ymd):
        path = os.path.join(dir, ymd)
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
        return path

    def _image_subscriber(self, writeImages, frames_written, eventQueue, publisher, view, outdir):
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
                    frames_written.value += 1
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

# Child subprocess subscribing to an OAK outpost's hi-res crop publisher (Plane 2, :5568).
# Only OAK-equipped outposts publish crops, so a CropStreamWriter is created only for nodes
# whose config carries a `crops` publisher address. Unlike ImageStreamWriter (event-gated,
# files named by capture timestamp), the crop stream is always-resident: each crop is self-
# identified by its sidecar and named from the correlation key. The crop socket is silent
# between events, so an always-on subscriber simply writes whatever the EventSampler emits.
class CropStreamWriter:

    def __init__(self, node_view, publisher, cropdir):
        self.node_view = node_view
        self.publisher = publisher
        self.cropdir = cropdir
        self.restarts = 0  # watchdog re-arms since startup, reported in HC
        self._frames_written = multiprocessing.Value('L', 0)  # monotonic crop counter for HC
        self._spawn()

    def _spawn(self):
        self.process = multiprocessing.Process(target=self._crop_subscriber, args=(
            self._frames_written, self.publisher, self.node_view[1], self.cropdir))
        self.process.start()
        logging.debug(f"CropStreamWriter started for {self.node_view} pid {self.process.pid} in {self.cropdir}")

    def restart(self):
        """Re-arm the crop subscriber with a fresh process, hence a fresh socket.

        The only reliable way to abandon a ZMQ connection that the transport
        still believes is alive (see CropSubscriber's heartbeat note) is to
        discard the context that owns it. Nothing is lost: the watchdog only
        calls this after minutes without a written crop, which means the child
        is parked in receive() rather than mid-write. The crop counter survives
        the restart — it reconciles against a cumulative crp record count."""
        self.restarts += 1
        logging.warning(f"CropStreamWriter re-arm #{self.restarts} for {self.node_view} "
                        f"(pid {self.process.pid}, {self._frames_written.value} crops written)")
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=5)
        self._spawn()

    def _set_datedir(self, dir, ymd):
        path = os.path.join(dir, ymd)
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
        return path

    def _crop_subscriber(self, frames_written, publisher, view, outdir):
        receiver = CropSubscriber(publisher, view)
        receiver.start()
        while True:
            try:
                text, jpeg = receiver.receive()  # blocks until a crop arrives
            except Exception:
                logging.exception(f"CropStreamWriter failure {self.node_view}")
                continue
            # Sidecar: {view}|crop_{class}|{event_id}|{tid}|{seqnum}|{phase}
            _view, _cropclass, event_id, tid, seqnum, phase = text.split('|')
            clas = _cropclass[len('crop_'):]
            # PROVISIONAL date partition — receive time, NOT capture time. The sidecar
            # carries no capture timestamp (only the crp OTE record does), so a crop
            # received just after midnight could land in a different day-folder than its
            # crp record. Clean fix is to add a capture ts to the sidecar so both planes
            # date identically; until then this is the one-line swap point.
            date_directory = self._set_datedir(outdir, str(date.today()))
            cropfile = "{}_{}_{}_{}_{}.jpg".format(event_id, tid, seqnum, clas, phase)
            try:
                with open(os.path.join(date_directory, cropfile), "wb") as f:
                    f.write(jpeg)
                frames_written.value += 1
            except Exception:
                logging.exception(f"CropStreamWriter write failure {self.node_view} {cropfile}")

# Child subprocess for managing the camwatcher index. There will be one instance of this daemon subprocess
# per camwatcher. It provides a single point of control for all updates to the camwatcher event index files.
# Rather than update the index directly, all CSV writers pass new index entries through a queue for update
# here. The datapump also passes any event delete commands it receives into this same gauntlet via the
# control socket. Allowing multiple processes to update the same filesystem object in an uncontrolled fashion
# is a direct path to chaos, destruction, and despair.
class CSVindex:

    CSV_new = 1
    CSV_delete = 2
    CSV_delete_images = 3

    # Purge pacing: yield briefly every this many files unlinked, and once between
    # globs, so deletion stays background work without stretching minutes of unlinks
    # into hours. See _purge_loop for the measurements behind these.
    PURGE_YIELD_FILES = 100
    PURGE_YIELD_SECS = 0.05

    def __init__(self, indxQ, alertQ):
        self.process = multiprocessing.Process(target=self._run, args=(indxQ, alertQ))
        self.process.start()
        logging.debug(f"CSVindex subprocess started, pid {self.process.pid}")

    def _run(self, indxQ, alertQ):
        _csvdir = CFG['data']['csvfiles']
        _imgdir = CFG['data']['images']
        _cropdir = CFG['data'].get('crops')  # OAK crop store; absent on non-crop datasinks
        _delQ = queue.Queue()
        _thread = threading.Thread(target=self._purge_loop, args=(_delQ,))
        _thread.daemon = True
        _thread.start()
        _sentinel_alert = alertQ
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
                # Not every event owns every kind of data — a picamera node publishes no
                # crops at all, an event whose scenes already expired owns no frames, and a
                # short traversal may produce neither. A glob that matches nothing is a
                # normal outcome of deletion, and the purge loop treats it as one.
                _delQ.put(os.path.join(_csvdir, _date, _event + '*'))
                _delQ.put(os.path.join(_imgdir, _date, _event + '*'))
                if _cropdir:  # remove any OAK crop JPEGs for this event
                    _delQ.put(os.path.join(_cropdir, _date, _event + '*'))
                # Send deletion alert to sentinel for re-broadcast to subscribers
                alert = {
                    'task': 'ALERT',
                    'payload': {
                        'flag': 'DEL',
                        'date': _date,
                        'event': _event,
                        'sink': CFG['sentinel']['datasink'],
                        'pump': CFG['sentinel']['datapump']
                    }
                }
                _sentinel_alert.put(alert)

            elif cmd == CSVindex.CSV_delete_images:
                # Scene expiry. The event SURVIVES: index row, tracking CSVs, and crops are all
                # left alone and only the scene frames are purged. Retention for the replay
                # footage is a separate clock from retention for the record of what happened —
                # the frames are 99% of the storage and the first thing to lose its value.
                #
                # Deliberately silent: no DEL alert. The event still exists and must stay in
                # every subscriber's event list. A kiosk holding a cached frame list for this
                # event discovers the expiry on access, where the datapump's missing-file
                # placeholder is treated as end-of-data.
                # Quiet and idempotent: an event whose frames are already gone is a normal
                # re-issue (the expiry band overlaps runs), not a failure to log.
                (_date, _event) = msg
                _delQ.put(os.path.join(_imgdir, _date, _event + '*'))

    def _purge_files(self, pattern) -> int:
        """Unlink everything matching a glob, yielding as the work is done.

        Returns the number of files removed. A pattern matching nothing removes
        nothing and is not an error — see the note at the CSV_delete producer."""
        removed = 0
        for i, path in enumerate(glob.glob(pattern), start=1):
            try:
                os.unlink(path)
                removed += 1
            except FileNotFoundError:
                pass  # already gone; purging is idempotent by design
            if i % CSVindex.PURGE_YIELD_FILES == 0:
                sleep(CSVindex.PURGE_YIELD_SECS)
        return removed

    def _purge_loop(self, delQ):
        """Delete data files for removed events and expired scenes.

        Deletion is background work and must not compete with the writers for the
        datasink's resources — but the throttle has to be aimed at the resource
        actually under contention. Measured on data1 (Pi 5, NVMe): unlinking a
        full event's 300 frames takes 9ms, and nearly all the cost of the previous
        shell-out implementation was spawning sh+ls+xargs+rm per glob, not disk.
        A flat 2s sleep per command therefore paced ~9ms of work with 2000ms of
        idling; and since get() already blocks on an empty queue, it did nothing
        whatsoever except when a backlog existed — precisely when the work should
        have been moving. One night's cleanup stretched two minutes of unlinks
        across eight hours, overlapping every other overnight job instead of
        finishing well ahead of them.

        So: unlink directly, no subprocess, and yield by files handled rather than
        by glob issued, so the pause tracks the work actually done."""
        while True:
            pattern = delQ.get()
            try:
                removed = self._purge_files(pattern)
                if removed:
                    logging.debug(f"CSVindex purged {removed} files matching '{pattern}'")
            except Exception:
                logging.exception(f"CSVindex data deletion failure for '{pattern}'")
            delQ.task_done()
            sleep(CSVindex.PURGE_YIELD_SECS)

# Disk I/O CSV writer thread
class CSVwriter:

    # Per-type CSV schemas. `trk` (and sentinel result types like `fd1`) carry
    # detection geometry; `crp` carries the crop correlation key only — no bbox
    # (geometry joins back to trk via seqnum/detidx, see plan §4.5).
    TRK_HEADER = "timestamp,objid,classname,rect_x1,rect_y1,rect_x2,rect_y2\n"
    CRP_HEADER = "timestamp,objid,seqnum,detidx,classname,phase\n"

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

    def _header(self, tag):
        return CSVwriter.CRP_HEADER if tag == 'crp' else CSVwriter.TRK_HEADER

    def _format_row(self, tag, d):
        if tag == 'crp':
            return ','.join([d['timestamp'], str(d['obj']), str(d['seq']),
                             str(d['det']), str(d['clas']), str(d['phase'])]) + "\n"
        return ','.join([d['timestamp'], str(d['obj']), str(d['clas']),
                         str(d['rect'][0]), str(d['rect'][1]),
                         str(d['rect'][2]), str(d['rect'][3])]) + "\n"

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
                    if _recType == 'start':
                        f = open(os.path.join(self._set_index(
                            _node, _view, _data['id'], _data['timestamp'], _data['camsize'], _tag, _data['new']),
                            _data['id'] + '_' + _tag + '.csv'), mode='wt')
                        f.write(self._header(_tag))  # column headers per record type
                        self._openfiles[_ref] = f # add to list
                    elif _recType == 'end':
                        logging.debug(f"CSVwriter closing file for {_ref}")
                        self._openfiles[_ref].close() # close file
                        del self._openfiles[_ref] # remove from list
                    elif _recType == _tag:
                        self._openfiles[_ref].write(self._format_row(_tag, _data))
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

    def _capture_health_record(self, record, csvpath):
        """Write a HEALTH record to daily JSONL file on the datasink."""
        date_key = record.get('date', datetime.now().strftime('%Y-%m-%d'))
        health_dir = os.path.join(csvpath, date_key)
        os.makedirs(health_dir, exist_ok=True)
        filepath = os.path.join(health_dir, 'health.json')
        try:
            with open(filepath, 'a') as f:
                f.write(json.dumps(record) + '\n')
            logging.info(f"HEALTH record captured: {filepath}")
        except OSError as e:
            logging.error(f"Failed to write health record: {e}")

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
                        _flag = logdata['flag']
                        if _flag in ['SUBMIT', 'START','EOJ']:
                            _jobid = logdata['jobid']
                        elif _flag == 'HEALTH':
                            self._capture_health_record(logdata, data['csvfiles'])
                            continue
                        else:
                            logging.debug(f"Sentinel alert received: {message}")
                            continue
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
        ekey = (node, view, eventID)
        if ote["type"] == 'trk':
            dbLogMsgQ.put(ote2db)
        elif ote["type"] == 'crp':
            # Crop correlation record (OAK). The crop stream is not bracketed by
            # start/end, so CamWatcher originates the crp file + index row on the
            # first crp seen for an event (synthetic 'start', mirroring SentinelAgent),
            # carrying the event's scene camsize cached from the trk 'start'.
            crp_ref = (node, view, eventID, 'crp')
            if ekey not in _crp_active:
                _crp_active.add(ekey)
                _camsize, _start_ts = _event_meta.get(ekey, ((0, 0), ote['timestamp']))
                dbLogMsgQ.put((crp_ref, {
                    "view": view, "id": eventID, "timestamp": _start_ts,
                    "type": "start", "new": True, "camsize": _camsize,
                }))
            dbLogMsgQ.put((crp_ref, ote))
            _crp_record_counts[node_view] = _crp_record_counts.get(node_view, 0) + 1
        elif ote["type"] == 'start':
            _event_meta[ekey] = (ote['camsize'], ote['timestamp'])
            dbLogMsgQ.put(ote2db)
            if node_view in outposts:
                # Start image subscriber / JPEG file writer
                outposts[node_view].start(eventID)
            else:
                logging.error(f"ImageStreamWriter {node_view} not found")
        elif ote["type"] == 'end':
            dbLogMsgQ.put(ote2db)
            if ekey in _crp_active:  # close the crp correlation file for this event
                dbLogMsgQ.put(((node, view, eventID, 'crp'), {"type": "end"}))
                _crp_active.discard(ekey)
            _event_meta.pop(ekey, None)
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
                node_name = topics[0]
                # Parse FPS from heartbeat: fps(tick_count, looks, events, tick_rate, measured_fps)
                try:
                    fps_data = message[3:].strip()
                    # Extract measured_fps from the last comma-separated value in parens
                    parts = fps_data.strip('()').split(',')
                    measured_fps = float(parts[-1].strip()) if len(parts) >= 5 else None
                except (ValueError, IndexError):
                    measured_fps = None
                if node_name not in outpost_health:
                    outpost_health[node_name] = OutpostHealth(node_name)
                outpost_health[node_name].record_heartbeat(measured_fps)
                logging.info(f"Outpost health '{node_name}' {message[3:]}")
            else:  # pass everything else along to the logger
                await dispatch_logger(topics, message)
        else:
            await dispatch_logger(topics, message)

def _build_hc_response():
    """Build health check response from CamWatcher internal state."""
    now = datetime.now()
    uptime = now - _start_time
    # Writer status
    writers = {}
    crop_writers_hc = {}
    with threadLock:
        for nv, writer in outposts.items():
            key = f"{nv[0]}/{nv[1]}"
            alive = writer.process.is_alive()
            writers[key] = {
                "alive": alive,
                "pid": writer.process.pid if alive else None,
                "frames_written": writer._frames_written.value
            }
        # OAK crop writers (only crop-publishing nodes). crops_written (files, image
        # plane) vs crp_records (records, log plane) reconcile the two planes — a
        # persistent gap signals crop/crp pair failures.
        for nv, writer in crop_writers.items():
            key = f"{nv[0]}/{nv[1]}"
            alive = writer.process.is_alive()
            crop_writers_hc[key] = {
                "alive": alive,
                "pid": writer.process.pid if alive else None,
                "crops_written": writer._frames_written.value,
                "crp_records": _crp_record_counts.get(nv, 0),
                "restarts": writer.restarts
            }
    # SentinelAgent liveness
    agent_alive = _sentinel_agent.process.is_alive() if _sentinel_agent is not None else None
    # Heartbeat status
    heartbeats = {}
    for node_name, health in outpost_health.items():
        heartbeats[node_name] = {
            "last_seen": health.last_heartbeat.isoformat() if health.last_heartbeat else None,
            "fps": health.last_fps,
            "age_seconds": round(health.age_seconds()) if health.age_seconds() is not None else None
        }
    # Disk usage
    disk = {}
    try:
        data_path = CFG['data']['images']
        usage = shutil.disk_usage(data_path)
        disk = {
            "total_gb": round(usage.total / (1024**3), 1),
            "used_gb": round(usage.used / (1024**3), 1),
            "percent": round(usage.used / usage.total * 100, 1)
        }
    except Exception:
        pass
    return {
        "flag": "HC",
        "component": "camwatcher",
        "timestamp": now.isoformat(),
        "uptime": str(uptime).split('.')[0],
        "writers": writers,
        "crop_writers": crop_writers_hc,
        "heartbeats": heartbeats,
        "disk": disk,
        "sentinel_agent": {"alive": agent_alive}
    }

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
                            if 'crops' in request:  # OAK outposts advertise a crop publisher
                                crop_writers[_new_outpost] = CropStreamWriter(_new_outpost, request['crops'], CFG['data']['crops'])
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
                elif request['cmd'] == 'DelImg':
                    # Scene expiry: drop the frames, keep the event (index row, CSVs, crops).
                    # A separate command rather than a scope field on DelEvt so that an older
                    # camwatcher cannot mistake it for a full delete — it falls through to the
                    # error branch below and removes nothing.
                    dateIndxQ.put((CSVindex.CSV_delete_images, (request['date'], request['event'])))
                elif request['cmd'] == 'HC':
                    result = json.dumps(_build_hc_response())
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

async def alert_sender():
    """Send alerts to sentinel for system-wide re-broadcast"""
    logging.info("Alert sender started")
    asyncCtx = AsyncContext.instance()
    while True:
        await asyncio.sleep(0.1)  # Async-friendly polling
        try:
            alert = sentinel_alertQ.get_nowait()
            with asyncCtx.socket(zmq.REQ) as sock:
                sock.connect(CFG['sentinel']['requests'])
                await sock.send(json.dumps(alert).encode('ascii'))
                reply = await sock.recv()
                if reply != b'OK':
                    logging.warning(f"Alert send failed: {reply}")
        except queue.Empty:
            pass
        except Exception:
            logging.exception("Alert sender trapped exception")

async def crop_writer_watchdog(interval=60, grace=180, max_grace=3600):
    """Cross-plane liveness check on the crop subscribers.

    A crop reaches the datasink over two independent sockets: the `crp`
    correlation record on the log plane, the JPEG on the crop plane. They fail
    independently, and the failure is silent — the index keeps claiming crops
    that have no file behind them, which downstream reads back as a placeholder
    image rather than an error (east, 2026-08-15).

    So: when the log plane says crops are being published and no file has been
    written for `grace` seconds, the crop socket is not delivering, whatever the
    reason, and the writer is re-armed. CropSubscriber's ZMTP heartbeat should
    get there first; this is the backstop that does not care about the cause.
    Idleness is not a stall — with no crp records arriving there is nothing to
    expect, so the stall clock only runs when the two planes disagree."""
    logging.info("crop writer watchdog started")
    last = {}  # (node,view) -> (crp_records, crops_written, last_progress, strikes)
    while True:
        await asyncio.sleep(interval)
        now = datetime.now()
        with threadLock:
            writers = list(crop_writers.items())
        for nv, writer in writers:
            crp, crops = _crp_record_counts.get(nv, 0), writer._frames_written.value
            (prev_crp, prev_crops, progress, strikes) = last.get(nv, (crp, crops, now, 0))
            if crops > prev_crops:
                progress, strikes = now, 0  # crops landing again; clean slate
            elif crp == prev_crp:
                progress = now              # nothing published, nothing to expect
            else:
                # Back off on repeat re-arms. A re-arm that does not restore the
                # stream means the fault is upstream — publisher down, network
                # partition — and respawning on a fixed cycle just adds noise to
                # an outage the datasink cannot fix from this end.
                patience = min(grace * (2 ** strikes), max_grace)
                if (now - progress).total_seconds() > patience:
                    logging.error(f"Crop stream stalled for {nv}: {crp - prev_crp} crp records "
                                  f"on the log plane, no crop written in {patience:.0f}s. Re-arming.")
                    with threadLock:
                        writer.restart()
                    progress, strikes, crops = now, strikes + 1, writer._frames_written.value
            last[nv] = (crp, crops, progress, strikes)

async def main():
    _data = CFG['data']
    logging.config.dictConfig(CFG['logconfigs']['camwatcher_internal'])
    log = logging.getLogger()
    global _sentinel_agent
    _csvidx = CSVindex(dateIndxQ, sentinel_alertQ)
    csv = CSVwriter(_data['csvfiles'], dateIndxQ, dbLogMsgQ)
    agent = SentinelAgent(CFG['sentinel'], dateIndxQ)
    _sentinel_agent = agent
    asyncCtx = AsyncContext.instance()
    asyncREP = asyncCtx.socket(zmq.REP)  # 0MQ async socket for control loop
    asyncSUB = asyncCtx.socket(zmq.SUB)  # 0MQ async socket for camwatcher log subscriptions
    # configure TCP keep-alive and reconnection options for the async SUB sockets
    asyncSUB.setsockopt(zmq.TCP_KEEPALIVE, 1)
    asyncSUB.setsockopt(zmq.TCP_KEEPALIVE_IDLE, 60)    # start probes after 60s idle
    asyncSUB.setsockopt(zmq.TCP_KEEPALIVE_INTVL, 10)   # probe every 10s
    asyncSUB.setsockopt(zmq.TCP_KEEPALIVE_CNT, 3)      # 3 failed probes = dead
    asyncSUB.setsockopt(zmq.RECONNECT_IVL, 1000)       # start at 1s
    asyncSUB.setsockopt(zmq.RECONNECT_IVL_MAX, 30000)  # cap at 30s
    asyncREP.bind(f"tcp://*:{CFG['control_port']}")
    asyncSUB.subscribe(b'')
    with threadLock:
        _outpost_nodes = CFG["outpost_nodes"]
        for node in _outpost_nodes:
            _nodecfg = _outpost_nodes[node]
            node_view = (node, _nodecfg['view'])
            outposts[node_view] = ImageStreamWriter(node_view, _nodecfg['images'], _data["images"])
            if 'crops' in _nodecfg:  # OAK outposts only — picamera nodes publish no crop stream
                crop_writers[node_view] = CropStreamWriter(node_view, _nodecfg['crops'], _data["crops"])
            asyncSUB.connect(_nodecfg['logger'])
    try:
        await asyncio.gather(control_loop(asyncREP, asyncSUB),
                             process_logs(asyncSUB, agent),
                             alert_sender(),
                             crop_writer_watchdog())
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
