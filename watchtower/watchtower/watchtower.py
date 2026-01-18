"""watchtower: Sentinelcam wall console, event and outpost viewer

Copyright (c) 2024 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import cv2
import enum
import numpy as np
import pandas as pd
import imutils
import json
import logging
import zmq
import threading
import queue
import msgpack
import multiprocessing
from multiprocessing import sharedctypes
import tkinter as tk
from tkinter import ttk
import PIL.Image, PIL.ImageTk
import simplejpeg
from ast import literal_eval
import time
from time import sleep
from datetime import date, datetime
from sentinelcam.datafeed import DataFeed
from sentinelcam.utils import FPS, ImageSubscriber, readConfig
from video_exporter import VideoExporter
from motion_calibration import MotionCalibrationPage

CFG = readConfig(os.path.join(os.path.expanduser("~"), "watchtower.yaml"))
SOCKDIR = CFG["socket_dir"]

class UserPage(enum.IntEnum):
    """User interface page types"""
    PLAYER = 0  # Main page, outpost and event viewer
    OUTPOSTS = 1  # List of outpost views to choose from
    EVENTS = 2  # Event list for current view
    SETTINGS = 3  # To be determined: settings, controls, and tools cafe
    CALIBRATE = 4  # Motion detector calibration tool

class PlayerCommand(enum.Enum):
    """Source command types for the Player subsystem"""
    VIEWER = 0  # datapump,outpost,viewname
    EVENT = 1   # datapump,viewname,date,event,imagesize

class DaemonState(enum.Enum):
    """State machine for the PlayerDaemon subprocess"""
    STOPPED = 0
    STARTED = 1
    ERROR = 2

class PlayerState(enum.Enum):
    """State machine for the Player subsystem"""
    STARTING = -1
    LOADING = 0
    READY = 1
    PLAYING = 2
    PAUSED = 3
    IDLE = 4
    ERROR = 5

class StateChange(enum.Enum):
    """State change reasons for the Player subsystem"""
    LOAD = 0
    READY = 1
    TOGGLE = 2
    AUTO = 3
    EOF = 4

# Configure logging
LOG_LEVEL = CFG.get("log_level", "WARN")
LOG_FORMAT = '%(asctime)s - %(levelname)s - %(processName)s - %(threadName)s - %(message)s'
LOG_FILE = CFG.get("log_file", os.path.join(os.path.expanduser("~"), "watchtower.log"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(LOG_FILE)
    ]
)

logger = logging.getLogger("watchtower")
logger.info("Watchtower starting")

# State transition logging helper
def log_state_transition(from_state, to_state, component, details=None):
    msg = f"State transition: {from_state} → {to_state} in {component}"
    if details:
        msg += f" ({details})"
    logger.debug(msg)

TRKCOLS = ["timestamp", "elapsed", "objid", "classname", "rect_x1", "rect_x2", "rect_y1", "rect_y2"]

dataLock = threading.Lock()

def blank_image(w, h) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    return img

def redx_image(w, h) -> np.ndarray:
    img = blank_image(w, h)
    cv2.line(img, (0, 0), (w - 1, h - 1), (0, 0, 255), 4)
    cv2.line(img, (0, h - 1), (w - 1, 0), (0, 0, 255), 4)
    return img

def convert_tkImage(cv2Image) -> PIL.ImageTk.PhotoImage:
    return PIL.ImageTk.PhotoImage(image=PIL.Image.fromarray(cv2.cvtColor(cv2Image, cv2.COLOR_BGR2RGB)))

class RingWire:
    def __init__(self, ipcname) -> None:
        self._wire = zmq.Context.instance().socket(zmq.REP)
        self._wire.bind(f"ipc://{ipcname}")
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
        if self.isEmpty():
            return -1
        else:
            return self._start

    def frame_complete(self) -> None:
        # Advance the start pointer only when the consuming process is done with it.
        # This avoids an inter-process race condition. After initially reading from
        # the buffer, invoking this just prior to subsequent get() operations prevents
        # the provider from overlaying the current frame in use.
        self._count -= 1
        self._start += 1
        self._start %= self._length

# multiprocessing class implementing a child subprocess for populating a ring buffer of images
class PlayerDaemon:
    def __init__(self, wirename, ringbuffers):
        self.wirename = wirename
        self.ringbuffers = ringbuffers
        self.datafeeds = {}
        self.commandQueue = multiprocessing.Queue()
        self.stateQueue = multiprocessing.Queue()
        self.runswitch = multiprocessing.Value('i', 0)
        self.process = multiprocessing.Process(target=self._data_monster, args=(
            self.commandQueue, self.stateQueue, self.runswitch, self.wirename, self.ringbuffers))
        self.process.start()

    def _setPump(self, pump) -> DataFeed:
        if not pump in self.datafeeds:
            self.datafeeds[pump] = DataFeed(pump, timeout=7.0)
        return self.datafeeds[pump]

    def _data_monster(self, commandQueue, stateQueue, keepgoing, wirename, ringbuffers):
        daemon_logger = logging.getLogger("watchtower.daemon")
        daemon_logger.info("PlayerDaemon subprocess started")
        ringwire = RingWire(wirename)
        # Wait here for handshake from player thread in parent process
        handshake = ringwire.recv()
        ringwire.send(handshake)  # acknowledge and get started
        daemon_logger.debug("Ring wire handshake completed")
        frametimes = []
        frameidx = 0
        date, event, ring, receiver = None, None, None, None
        while True:
            cmd = commandQueue.get()
            # Connect to image source: outpost/view or datapump.
            # Establish ringbuffer selection by image size.
            # Reset the buffer, then recv, send, read, and iterate.
            # Keep ring buffer populated until stopped or out of data.
            if cmd[0] == PlayerCommand.VIEWER:
                (datapump, publisher, view) = cmd[1:]
                # This taps into a live image publication stream. There is
                # no end to this; it always represents current data capture.
                # Just keep going here forever until explicitly stopped.
                if not receiver:
                    receiver = ImageSubscriber(publisher, view)
                receiver.subscribe(publisher, view)
                receiver.start()
                try:
                    frame = simplejpeg.decode_jpeg(receiver.receive(timeout=7.0)[1], colorspace='BGR')
                    wh = (frame.shape[1], frame.shape[0])
                    if ring != wh:
                        ring = wh
                        ringbuffer = ringbuffers[ring]
                except TimeoutError as e:
                    daemon_logger.error(f"VIEWER mode: Initial connection timeout to {publisher}: {str(e)}")
                    stateQueue.put(DaemonState.ERROR)
                except KeyError as keyval:
                    daemon_logger.error(f"PlayerDaemon internal key error '{keyval}'")
                    stateQueue.put(DaemonState.ERROR)
                except Exception as e:
                    daemon_logger.exception(f"VIEWER mode: trapped exception reading from {publisher}: {str(e)}")
                    stateQueue.put(DaemonState.ERROR)
                else:
                    ringbuffer.reset()
                    ringbuffer.put(frame)
                    stateQueue.put(DaemonState.STARTED)  # Acknowledge started
                    started, error_occurred = False, False
                    while keepgoing.value:
                        if ringwire.ready():
                            msg = ringwire.recv()
                            if not started:
                                started = True
                            else:
                                ringbuffer.frame_complete()
                            if error_occurred:
                                ringwire.send(-1)
                            else:
                                ringwire.send(ringbuffer.get())
                        elif ringbuffer.isFull() or error_occurred:
                            sleep(0.005)
                        else:
                            try:
                                jpeg_data = receiver.receive(timeout=7.0)[1]
                                ringbuffer.put(simplejpeg.decode_jpeg(jpeg_data, colorspace='BGR'))
                            except TimeoutError as e:
                                daemon_logger.error(f"VIEWER mode: Timeout reading from {publisher}")
                                error_occurred = True
                            except Exception as e:
                                daemon_logger.exception(f"VIEWER mode: trapped exception reading from {publisher}: {str(e)}")
                                error_occurred = True
                    stateQueue.put(DaemonState.STOPPED)  # Acknowledge stopped
                    receiver.stop()
            else:
                (datapump, viewname, eventdate, eventid, imgsize) = cmd[1:]
                # Unlike a live outpost viewer, datapump events have a definite end. Maintain state and keep
                # the ring buffer populated. An exhausted ring buffer is considered EOF when reading in either
                # direction. Send an EOF response to the Player and reset to the beginning in either case.
                feed = self._setPump(datapump)
                try:
                    if ring != imgsize:
                        ring = imgsize
                        ringbuffer = ringbuffers[ring]
                    if (eventdate, eventid) != (date, event):
                        (date, event) = (eventdate, eventid)
                        ringbuffer.reset()
                        frametimes = feed.get_image_list(eventdate, eventid)
                        jpeg = feed.get_image_jpg(eventdate, eventid, frametimes[0])
                        ringbuffer.put(simplejpeg.decode_jpeg(jpeg, colorspace='BGR'))
                        forward = True
                        frameidx = 1
                except KeyError as keyval:
                    daemon_logger.error(f"PlayerDaemon internal key error '{keyval}'")
                    stateQueue.put(DaemonState.ERROR)
                except Exception as e:
                    daemon_logger.exception(f"EVENT mode: trapped exception {str(e)} error reading from datapump ({datapump},{eventdate},{eventid})")
                    stateQueue.put(DaemonState.ERROR)
                else:
                    started, error_occurred = False, False
                    stateQueue.put(DaemonState.STARTED)  # Acknowledge started
                    while keepgoing.value:
                        if ringwire.ready():
                            msg = ringwire.recv() # response here reserved for player commands, reverse/forward/other
                            if not started:
                                started = True
                            else:
                                ringbuffer.frame_complete()
                            if ringbuffer.isEmpty() or error_occurred:
                                ringwire.send(-1)
                                forward = True
                                frameidx = 0
                            else:
                                ringwire.send(ringbuffer.get())
                        elif ringbuffer.isFull() or error_occurred:
                            sleep(0.005)
                        else:
                            if (forward and frameidx < len(frametimes)) or (not forward and frameidx > -1):
                                try:
                                    jpeg = feed.get_image_jpg(eventdate, eventid, frametimes[frameidx])
                                    ringbuffer.put(simplejpeg.decode_jpeg(jpeg, colorspace='BGR'))
                                    frameidx = frameidx + 1 if forward else frameidx - 1
                                except Exception as e:
                                    daemon_logger.exception(f"EVENT mode: trapped exception {str(e)} reading from datapump ({datapump},{eventdate},{eventid})")
                                    error_occurred = True
                    stateQueue.put(DaemonState.STOPPED)  # Acknowledge stopped

    def start(self, command_block):
        self.runswitch.value = 1
        self.commandQueue.put(command_block)
        logger.debug(f"PlayerDaemon started with command: {command_block}")
        return self.stateQueue.get()  # Wait for acknowledgment

    def stop(self):
        self.runswitch.value = 0
        logger.debug("PlayerDaemon stopped.")
        return self.stateQueue.get()  # Wait for acknowledgment

class TextHelper:
    def __init__(self) -> None:
        self._lineType = cv2.LINE_AA
        self._textType = cv2.FONT_HERSHEY_SIMPLEX
        self._textSize = 0.5
        self._thickness = 1
        self._textColors = {}
        self._bboxColors = {}
        self.setColors(['Unknown'])
    def setTextColor(self, bgr) -> tuple:
        luminance = ((bgr[0]*.114)+(bgr[1]*.587)+(bgr[2]*.299))/255
        return (0,0,0) if luminance > 0.5 else (255,255,255)
    def setColors(self, names) -> None:
        for name in names:
            if name not in self._bboxColors:
                self._bboxColors[name] = tuple(int(x) for x in np.random.randint(256, size=3))
                self._textColors[name] = self.setTextColor(self._bboxColors[name])
    def putText(self, frame, objid, text, x1, y1, x2, y2) -> None:
        (tw, th) = cv2.getTextSize(text, self._textType, self._textSize, self._thickness)[0]
        cv2.rectangle(frame, (x1, y1), (x2, y2), self._bboxColors[objid], 2)
        cv2.rectangle(frame, (x1, (y1 - 28)), ((x1 + tw + 10), y1), self._bboxColors[objid], cv2.FILLED)
        cv2.putText(frame, text, (x1 + 5, y1 - 10), self._textType, self._textSize, self._textColors[objid], self._thickness, self._lineType)

class EventAggregator:
    def __init__(self) -> None:
        self.datafeeds = {}
        self.current_pump = None
        self.texthelper = TextHelper()

    def _setPump(self, pump) -> DataFeed:
        if not pump in self.datafeeds:
            self.datafeeds[pump] = DataFeed(pump, timeout=7.0)
        return self.datafeeds[pump]

    def gatherEventResults(self, date, event, datapump) -> tuple:
        if datapump != self.current_pump:
            self.datafeed = self._setPump(datapump)
            self.current_pump = datapump
        cwIndx = self.datafeed.get_date_index(date)
        evtSets = cwIndx.loc[cwIndx['event'] == event]
        evtData = pd.DataFrame(columns=TRKCOLS)
        refsort = {'trk': 0, 'obj': 1, 'vsp': 2, 'fd1': 3, 'fr1': 4}  # z-ordering for tracking result labels
        if len(evtSets.index) > 0:
            trkTypes = [t for t in evtSets['type']]
            # Process each tracking type individually to handle missing data gracefully
            all_tracking_data = []
            for t in trkTypes:
                try:
                    tracking_data = self.datafeed.get_tracking_data(date, event, t)
                    all_tracking_data.append((t, tracking_data))
                except DataFeed.TrackingSetEmpty as e:
                    logger.debug(f"No tracking data for {e.date},{e.evt},{e.trk}")
                except Exception as e:
                    logger.error(f"Failure retrieving tracking data for {e.date},{e.evt},{e.trk}: {str(e)}")

            # Only proceed with concatenation if we have any data
            if all_tracking_data:
                try:
                    evtData = pd.concat([data for _, data in all_tracking_data],
                                      keys=[t for t, _ in all_tracking_data],
                                      names=['ref'])
                    evtData['name'] = evtData.apply(lambda x: str(x['classname']).split(':')[0], axis=1)
                    self.texthelper.setColors(evtData['name'].unique())
                except Exception as e:
                    logger.error(f"Error processing tracking data: {str(e)}")
            else:
                logger.debug(f"No valid tracking data found for any tracking types for {date},{event}")
        frametimes = []
        try:
            frametimes = self.datafeed.get_image_list(date, event)
        except DataFeed.ImageSetEmpty as e:
            logger.error(f"No image data for {e.date},{e.evt}")
        except Exception as e:
            logger.error(f"Failure retrieving image list for {e.date},{e.evt}: {str(e)}")
        refresults = tuple(
            tuple(
                (rec.name, rec.classname, rec.rect_x1, rec.rect_y1, rec.rect_x2, rec.rect_y2)
                    for rec in evtData.loc[evtData['timestamp'] == frametime].sort_values(
                        by=['ref'], key=lambda x: x.map(refsort)).itertuples()
            )
            for frametime in frametimes
        )
        return (frametimes, refresults)

class Player:
    def __init__(self, dataReady, source_queue, wirename, rawbuffers, views, state_manager) -> None:
        self.setup_ringbuffers(rawbuffers)
        self.ringWire_connection(wirename)
        self.idle = threading.Event()
        self.paused = threading.Event()
        self.event_aggregator = EventAggregator()
        self.fps = FPS()
        self.datafeeds = {}
        self.datafeed = None
        self.current_pump = None
        self.outpost_views = views
        self.state_manager = state_manager

        self._thread = threading.Thread(daemon=True, target=self._playerThread, args=(dataReady, source_queue))
        self._thread.start()

    def setup_ringbuffers(self, rawbuffers):
        self.ringbuffers = {}
        for wh in rawbuffers:
            dtype = np.dtype('uint8')
            shape = (wh[1], wh[0], 3)
            self.ringbuffers[wh] = [np.frombuffer(buffer, dtype=dtype).reshape(shape) for buffer in rawbuffers[wh]]

    def ringWire_connection(self, wirename):
        self.ringWire = zmq.Context.instance().socket(zmq.REQ)
        self.ringWire.connect(f"ipc://{wirename}")
        self.ringWire.send(msgpack.packb(0))  # send the ready handshake
        self.ringWire.recv()                  # wait for player daemon response
        self.poller = zmq.Poller()
        self.poller.register(self.ringWire, zmq.POLLIN)

    def get_bucket(self) -> int:
        self.ringWire.send(msgpack.packb(0))
        if dict(self.poller.poll(1000)):
            bucket = msgpack.unpackb(self.ringWire.recv())
            return bucket
        else:
            return -1

    def set_imgdata(self, image) -> None:
        self.image = image

    def get_imgdata(self) -> np.ndarray:
        return (self.image)

    def _playerThread(self, dataReady, source_queue) -> None:
        self.paused.set()
        image = blank_image(1,1)
        logger.debug(f"Player thread started.")
        while True:
            datasource = source_queue.get()
            cmd = datasource[0]
            imgsize = datasource[1]
            logger.debug(f"Player thread has new data source command: {cmd}")
            # Setup the Player for a new camera view/event
            ringbuffer = self.ringbuffers[imgsize]
            refresults = ()
            frametimes = []
            frameidx = 0
            forward = True
            if cmd[0] == PlayerCommand.EVENT:
                (view, date, event, size) = cmd[2:]
                # For events, retrieve all tracking data and the list of image timestamps. First,
                # apply a blur effect to the player display as visible feedback to the button press.
                self.set_imgdata(cv2.blur(image, (15, 15)))
                dataReady.set()
                evtkey = (date, event)
                if evtkey in self.outpost_views[view].eventCache:
                    logger.debug(f"Using cached event data for {evtkey}")
                    (frametimes, refresults) = self.outpost_views[view].get_cache(evtkey)
                else:
                    logger.debug(f"Gathering event results for {evtkey}")
                    (frametimes, refresults) = self.event_aggregator.gatherEventResults(date, event, cmd[1])
                    self.outpost_views[view].set_cache(evtkey, frametimes, refresults)
                when = frametimes[0].strftime('%I:%M %p - %A %B %d, %Y') if len(frametimes) > 0 else ''
            else:
                view = cmd[3]
                when = 'current view'

            dataReady.clear()
            status_message = view + ' ' + when
            self.state_manager.request_transition(StateChange.READY, "PlayerThread")
            source_queue.task_done()

            while source_queue.empty():
                if self.paused.is_set():
                    self.idle.set()
                    sleep(0.01)
                else:
                    if dataReady.is_set():
                        sleep(0.005)
                    else:
                        try:
                            bucket = self.get_bucket()
                            if bucket != -1:
                                image = ringbuffer[bucket]

                                if CFG['viewfps']:
                                    self.fps.update()
                                    text = "FPS: {:.2f}".format(self.fps.fps())
                                    text_color = (255,255,255)  # fixed white for fps display
                                    # Get text size for background
                                    (text_width, text_height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                                    # Draw semi-transparent background
                                    overlay = image.copy()
                                    cv2.rectangle(overlay, (5, image.shape[0]-25), (15 + text_width, image.shape[0]-5), (0, 0, 0), -1)
                                    cv2.addWeighted(overlay, 0.5, image, 0.5, 0, image)
                                    cv2.putText(image, text, (10, image.shape[0]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, text_color, 1)

                                if cmd[0] == PlayerCommand.EVENT:
                                    for (name, classname, x1, y1, x2, y2) in refresults[frameidx]:
                                        self.event_aggregator.texthelper.putText(image, name, classname, x1, y1, x2, y2)

                                    if frameidx < len(frametimes) - 1:
                                        frameidx += 1

                                        if forward:
                                            # whenever elapsed time within event > playback elapsed time,
                                            # estimate a sleep time to dial back the replay framerate
                                            frame_elaps = frametimes[frameidx] - frametimes[frameidx-1]
                                            playback_elaps = datetime.now() - self.last_frame
                                            if frame_elaps > playback_elaps:
                                                pause = frame_elaps - playback_elaps
                                                time.sleep(pause.seconds + pause.microseconds/1000000)
                                else:
                                    frameidx += 1

                                if frameidx < 60:
                                    text_color = (255,255,255)  # fixed white for status message
                                    # Get text size for background
                                    (text_width, text_height), baseline = cv2.getTextSize(status_message, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
                                    # Draw semi-transparent background
                                    overlay = image.copy()
                                    cv2.rectangle(overlay, (15, 20), (25 + text_width, 50 + baseline), (0, 0, 0), -1)
                                    cv2.addWeighted(overlay, 0.5, image, 0.5, 0, image)
                                    cv2.putText(image, status_message, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color, 1)

                                self.set_imgdata(image)
                                dataReady.set()
                                self.last_frame = datetime.now()

                            else:
                                self.state_manager.request_transition(StateChange.EOF, "PlayerThread")
                                self.paused.set()
                                frameidx = 0

                        except IndexError as ex:
                            logger.error(f"IndexError cmd={cmd[0]} frameidx={frameidx} of {len(frametimes)}")
                        except Exception as ex:
                            logger.exception(f'Unhandled exception caught in Player thread: {str(ex)}')

    def stop(self) -> None:
        logger.debug("Player thread paused.")
        self.paused.set()
        self.idle.wait()

    def start(self) -> None:
        logger.debug("Player thread resumed.")
        self.last_frame = datetime.now()
        self.paused.clear()
        self.idle.clear()
        self.fps.reset()

class PlayerStateManager:
    def __init__(self, app):
        self.app = app
        self.message_queue = queue.Queue()
        self.current_state = PlayerState.STARTING
        self.player_command = None
        self.cursor_event_idx = -1

    def request_transition(self, reason, component, details=None):
        logger.debug(f"Requesting transition from {self.current_state}: {reason}, {component}")

        if reason == StateChange.LOAD:
            if self.current_state == PlayerState.LOADING:
                logger.debug(f"Ignoring LOAD request while already in LOADING state from {component}")
                return  # Reject rapid LOAD requests, wait for current load to complete
            if self.current_state == PlayerState.PLAYING:
                self.message_queue.put((StateChange.TOGGLE, component, 'PlayerStateManager auto-pause before LOAD'))

        elif reason == StateChange.EOF:
            if self.app.move_next:
                if self.cursor_event_idx < self.app.view.event_count():
                    self.cursor_event_idx += 1
                    logger.debug(f"Auto-advance: queueing event {self.cursor_event_idx} of {self.app.eventIdx}")
                    (dt, date, event, size) = self.app.view.eventlist[self.cursor_event_idx]
                    source_cmd = ((PlayerCommand.EVENT, self.player_command[1], self.player_command[2],
                                   date, event, size), size)
                    self.message_queue.put((StateChange.LOAD, "auto-advance", source_cmd))
                else:
                    self.app.move_next = False
                    self.app.select_outpost_view(auto_play=True)
                    logger.debug("Auto-advance: no more events, stopping playback.")
                    return

        self.message_queue.put((reason, component, details))

    def process_transitions(self):
        try:
            while not self.message_queue.empty():
                reason, component, details = self.message_queue.get_nowait()
                logger.debug(f"Processing state transition: {reason}, {component}")
                old_state = self.current_state

                if reason == StateChange.LOAD:
                    self.app.sourceCmds.put(details)
                    self.player_command = details[0]
                    new_state = PlayerState.LOADING
                    if self.player_command[0] == PlayerCommand.EVENT:
                        self.last_event = self.player_command[2:-1]

                elif reason == StateChange.READY:
                    new_state = PlayerState.READY
                    if self.app.auto_play:
                        result = self.app.player_daemon.start(self.player_command)
                        if result == DaemonState.ERROR:
                            new_state = PlayerState.ERROR
                        else:
                            self.app.viewer.start()
                            new_state = PlayerState.PLAYING

                elif reason == StateChange.TOGGLE:
                    if self.current_state == PlayerState.ERROR:
                        new_state = PlayerState.ERROR
                    elif self.current_state == PlayerState.PLAYING:
                        self.app.viewer.stop()
                        self.app.player_daemon.stop()
                        self.app.move_next = False
                        new_state = PlayerState.PAUSED
                    else:
                        result = self.app.player_daemon.start(self.player_command)
                        if result == DaemonState.ERROR:
                            new_state = PlayerState.ERROR
                        else:
                            self.app.viewer.start()
                            new_state = PlayerState.PLAYING

                elif reason == StateChange.AUTO:
                    self.cursor_event_idx = details
                    new_state = self.current_state

                elif reason == StateChange.EOF:
                    self.app.player_daemon.stop()
                    new_state = PlayerState.IDLE

                if new_state in [PlayerState.PLAYING, PlayerState.PAUSED, PlayerState.READY, PlayerState.IDLE]:
                    self.app.player_panel.update_state(is_paused=(new_state != PlayerState.PLAYING))

                elif new_state == PlayerState.ERROR:
                    self.app.player_panel.update_image(redx_image(800,480))
                    self.app.player_panel.update_state(is_paused=True)
                    self.app.player_panel.show_buttons()

                log_state_transition(old_state, new_state, component, details)
                self.current_state = new_state

        except queue.Empty:
            pass

class SentinelSubscriber:
    def __init__(self, sentinel) -> None:
        self.eventQueue = multiprocessing.Queue()
        self.process = multiprocessing.Process(target=self._sentinel_reader, args=(
            sentinel, self.eventQueue))
        self.process.start()

    def _sentinel_reader(self, sentinel, eventQueue):
        daemon_logger = logging.getLogger("watchtower.SentinelSubscriber")
        # subscribe to Sentinel result publication
        sentinel_log = zmq.Context.instance().socket(zmq.SUB)
        sentinel_log.subscribe(b'')
        sentinel_log.connect(sentinel)
        event_lists = {}
        # consume every logging record published from the sentinel, watching for new events
        daemon_logger.info("SentinelSubscriber started.")
        while True:
            topic, msg = sentinel_log.recv_multipart()
            topics = topic.decode('utf8').strip().split('.')
            message = msg.decode('ascii')
            if topics[1] == 'INFO' and message[0] == '{':
                try:
                    logdata = json.loads(message)
                    if 'flag' in logdata:
                        if logdata['flag'] == 'EOJ' and logdata['event'] is not None:
                            viewkey = (logdata['from'][0], logdata['from'][1], logdata['sink'])
                            evtkey = (logdata['date'], logdata['event'], logdata['pump'])
                            task = logdata['task']
                            if viewkey in event_lists:
                                if evtkey not in event_lists[viewkey]:
                                    event_lists[viewkey].append(evtkey)
                                    eventQueue.put((EventListUpdater.EventList_NEW, viewkey, evtkey))
                                    daemon_logger.debug(f"Sentinel subscriber appended new eventkey {evtkey} for view {viewkey} from task {task}")
                            else:
                                event_lists[viewkey] = [evtkey]
                                eventQueue.put((EventListUpdater.EventList_NEW, viewkey, evtkey))
                                daemon_logger.debug(f"Sentinel subscriber added new eventkey {evtkey} for view {viewkey} from task {task}")
                        elif logdata['flag'] == 'DEL':
                            # Event deleted from datasink, purge from all view event lists
                            evtkey = (logdata['date'], logdata['event'], logdata['pump'])
                            for (viewkey, view_events) in event_lists.items():
                                if evtkey in view_events:
                                    eventQueue.put((EventListUpdater.EventList_DELETE, viewkey, evtkey))
                                    view_events.remove(evtkey)
                                    break
                            daemon_logger.debug(f"Sentinel subscriber purged deleted eventkey {evtkey} from view {viewkey}")
                except (KeyError, ValueError):
                    pass
                except Exception as e:
                    daemon_logger.exception(f"Exception parsing sentinel log '{message}': {str(e)}")

class EventListUpdater:

    EventList_NEW = 1
    EventList_DELETE = 2

    def __init__(self, eventQ, newEvent, outpost_views):
        self._eventData = (None, None)
        self.outpost_views = outpost_views
        self.event_aggregator = EventAggregator()
        self._image = blank_image(1,1)
        self._thread = threading.Thread(daemon=True, target=self._run, args=(eventQ, newEvent, outpost_views))
        self._thread.start()

    def getEventData(self) -> tuple:
        """Get the current event data and image"""
        return (self._eventData, self._image)

    def _sample_event_image(self, feed, day, event, pump, view, imgsize):
        try:
            (frametimes, refresults) = self.event_aggregator.gatherEventResults(day, event, pump)
            # Select sample image: find index with most refresults, then pick centermost if multiple
            if frametimes and refresults:
                # Find all indices with the max number of refresults
                max_count = max(len(r) for r in refresults)
                candidate_indices = [i for i, r in enumerate(refresults) if len(r) == max_count]
                # Pick the centermost index among candidates
                sample_frame = candidate_indices[len(candidate_indices) // 2]
                image = simplejpeg.decode_jpeg(
                    feed.get_image_jpg(day, event, frametimes[sample_frame]),
                    colorspace='BGR')

                header_text = f"{view} {frametimes[sample_frame].strftime('%I:%M %p - %A %B %d, %Y')}"
                # Get text size for background rectangle
                (text_width, text_height), baseline = cv2.getTextSize(header_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
                # Draw semi-transparent background
                overlay = image.copy()
                cv2.rectangle(overlay, (15, 20), (30 + text_width, 50 + baseline), (0, 0, 0), -1)
                cv2.addWeighted(overlay, 0.5, image, 0.5, 0, image)
                # Draw text on top
                cv2.putText(image, header_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

                for (name, classname, x1, y1, x2, y2) in refresults[sample_frame]:
                    self.event_aggregator.texthelper.putText(image, name, classname, x1, y1, x2, y2)

                return (frametimes[sample_frame], image)
            elif frametimes:
                logger.error(f"No tracking data for event {event}, returning first image.")
                return (frametimes[0], blank_image(imgsize[0], imgsize[1]))
            else:
                logger.error(f"Failed to gather images for event {event}")
                return (datetime.now(), blank_image(imgsize[0], imgsize[1]))
        except (DataFeed.TrackingSetEmpty, DataFeed.ImageSetEmpty):
            # Event deleted or incomplete - use blank thumbnail
            logger.error(f"Event {event} unavailable (deleted or incomplete)")
            return (datetime.now(), blank_image(imgsize[0], imgsize[1]))
        except Exception:
            logger.exception(f"EventListUpdater gather thumbnail for [{view}]")
            return (datetime.now(), blank_image(imgsize[0], imgsize[1]))

    def _run(self, eventQ, newEvent, outpost_views):
        datafeeds = {}
        sink_events = {}
        day = str(date.today())
        # populate an initial event list for each view
        for (sink, pump) in CFG['datapumps'].items():
            if not pump in datafeeds:
                datafeeds[pump] = DataFeed(pump)
            feed = datafeeds[pump]
            try:
                cwIndx = feed.get_date_index(day).sort_values('timestamp')
                sink_events[sink] = cwIndx.loc[cwIndx['type']=='trk']
            except Exception:
                logger.exception(f"EventListUpdater gather event list [{sink}]")
        for v in outpost_views.values():
            viewEvts = sink_events[v.sinktag].loc[
                (sink_events[v.sinktag]['node'] == v.node) &
                (sink_events[v.sinktag]['viewname'] == v.view)]
            if len(viewEvts.index) > 0:
                evtlist = [(rec.timestamp, day, rec.event, (rec.width, rec.height)) for rec in viewEvts.itertuples()]
                v.set_event_list(evtlist[:-1])
                self._eventData = (v.view, evtlist[-1])
                # with event data properly staged, select a sample image for the menu thumbnail
                try:
                    day = evtlist[-1][1]
                    event = evtlist[-1][2]
                    _, self._image = self._sample_event_image(
                        datafeeds[pump], day, event, pump, v.view, v.imgsize)
                except (DataFeed.TrackingSetEmpty, DataFeed.ImageSetEmpty):
                    # Event deleted or incomplete - use blank thumbnail
                    logger.info(f"Event {event} unavailable (deleted or incomplete)")
                    self._image = blank_image(v.imgsize[0], v.imgsize[1])
                except Exception:
                    logger.exception(f"EventListUpdater gather thumbnail for [{v.view}]")
                    self._image = blank_image(v.imgsize[0], v.imgsize[1])
                newEvent.set()
                while newEvent.is_set():
                    sleep(0.01)
                logger.debug(f"EventListUpdater initialized view {v.view} with event list of {len(evtlist)} events.")
        logger.debug('EventListUpdater started.')
        while True:
            logger.debug('EventListUpdater waiting for sentinel subscriber event...')
            (action, viewkey, evtkey) = eventQ.get()
            try:
                logger.debug(f"EventListUpdater received sentinel subscriber action {action} for viewkey {viewkey} evtkey {evtkey}")
                (node, view, sink) = viewkey
                (day, event, pump) = evtkey
                v = self.outpost_views[view]
                if action == EventListUpdater.EventList_NEW:
                    if not pump in datafeeds:
                        datafeeds[pump] = DataFeed(pump)
                    (event_time, self._image) = self._sample_event_image(
                        datafeeds[pump], day, event, pump, view, v.imgsize)
                    logger.debug(f"EventListUpdater sampled image for event {event} at {event_time}")
                    evtRef = (pd.Timestamp(event_time), day, event, v.imgsize)
                    self._eventData = (view, evtRef)
                    logger.debug(f"EventListUpdater signalled action {action} for view {view} eventref {evtRef}")
                    newEvent.set()
                    while newEvent.is_set():
                        sleep(0.01)
                else:  # EventList_DELETE
                    v.delete_event((day, event))
            except (DataFeed.TrackingSetEmpty, DataFeed.ImageSetEmpty):
                logger.info(f"Event {event} unavailable (deleted or incomplete)")
            except Exception:
                logger.exception("EventListUpdater trapped exception")

class OutpostView:
    def __init__(self, view, node, publisher, sinktag, datapump, imgsize, description) -> None:
        self.view = view
        self.node = node
        self.publisher = publisher
        self.sinktag = sinktag
        self.datapump = datapump
        self.imgsize = imgsize
        self.thumbnail = redx_image(213, 160)
        self.description = description
        self.menulabel = description
        self.menuref = None
        self.eventlist = []
        self.eventCache = {}  # Map of event -> (frametimes, refresults)
        self.max_events = CFG.get('max_events_per_view', 1500)  # Default to 1500 if not specified

    def store_menuref(self, menuitem) -> None:
        self.menuref = menuitem

    def event_count(self) -> int:
        with dataLock:
            return len(self.eventlist)

    def set_event_list(self, newlist) -> None:
        with dataLock:
            self.eventlist = newlist

    def add_event(self, event) -> None:
        logger.debug(f"OutpostView {self.view} adding event {event}")
        with dataLock:
            if len(self.eventlist) >= self.max_events:
                # Remove cached data for the oldest event before removing it
                old_event = self.eventlist[0]
                event_key = (old_event[1], old_event[2])
                if event_key in self.eventCache:
                    del self.eventCache[event_key]
                self.eventlist.pop(0)
            self.eventlist.append(event)
            self.update_label()

    def delete_event(self, event) -> None:
        logger.debug(f"OutpostView {self.view} deleting event {event}")
        with dataLock:
            for idx, evt in enumerate(self.eventlist):
                if (evt[1], evt[2]) == event:
                    self.eventlist.pop(idx)
                    if event in self.eventCache:
                        del self.eventCache[event]
                    break

    def set_cache(self, evtkey, frametimes, refresults) -> None:
        with dataLock:
            self.eventCache[evtkey] = (frametimes, refresults)

    def get_cache(self, evtkey) -> tuple:
        with dataLock:
            return self.eventCache.get(evtkey, ([], ()))

    def update_label(self) -> None:
        evt_time = self.eventlist[-1][0].to_pydatetime().strftime('%A %b %d, %I:%M %p')
        self.menulabel = f"{self.description}\n{evt_time}"

    def update_thumbnail(self, image):
        self.thumbnail = imutils.resize(image, width=213)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - #
#                                 All user interface logic follows below                                              #
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - #

class MenuPanel(ttk.Frame):
    ''' A button-press vertically scrolled frame; with credit due to a couple of posts on Stack Overflow.

           https://stackoverflow.com/questions/16188420/tkinter-scrollbar-for-frame/
           https://stackoverflow.com/questions/56165257/touchscreen-scrolling-tkinter-python

        Use the 'interior' attribute to place widgets inside the scrollable frame.
    '''
    def __init__(self, parent, width, height, show_scrollbar=False, visible_height=None):
        ttk.Frame.__init__(self, parent)
        # Create a canvas object and a vertical scrollbar for scrolling it.
        vscrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL)
        self.canvas = canvas = tk.Canvas(
            self, background="black", borderwidth=0, highlightthickness=0, yscrollcommand=vscrollbar.set)
        canvas.grid(row=0, column=0, sticky=(tk.N, tk.W, tk.S))
        if show_scrollbar:
            vscrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        vscrollbar.config(command=canvas.yview)

        self.canvaswidth = width
        self.canvasheight = height
        self.visible_height = visible_height if visible_height is not None else height

        # reset the view
        canvas.xview_moveto(0)
        canvas.yview_moveto(0)
        # create a frame inside the canvas which will be scrolled with it
        self.interior = interior = ttk.Frame(canvas, height=self.canvasheight, borderwidth=0)
        interior_id = canvas.create_window(0, 0, window=interior, anchor=tk.NW)
        # update the scrollbars to match the size of the inner frame
        size = (width, height) # scrollable content region
        canvas.config(scrollregion="0 0 %s %s" % size)
        # update the canvas width and height (visible viewport)
        canvas.config(width=self.canvaswidth, height=self.visible_height)
        # update the inner frame width to fill the canvas
        canvas.itemconfigure(interior_id, width=self.canvaswidth)

        self.offset_y = 0
        self.prevy = 0
        self.scrollposition = 1

        canvas.bind("<Enter>", lambda _: canvas.bind_all('<Button-1>', self.on_press), '+')
        canvas.bind("<Leave>", lambda _: canvas.unbind_all('<Button-1>'), '+')
        canvas.bind("<Enter>", lambda _: canvas.bind_all('<B1-Motion>', self.on_touch_scroll), '+')
        canvas.bind("<Leave>", lambda _: canvas.unbind_all('<B1-Motion>'), '+')

    def on_press(self, event):
        self.offset_y = event.y_root
        if self.scrollposition < 1:
            self.scrollposition = 1
        elif self.scrollposition > self.canvasheight:
            self.scrollposition = self.canvasheight
        self.canvas.yview_moveto(self.scrollposition / self.canvasheight)

    def on_touch_scroll(self, event):
        nowy = event.y_root
        sectionmoved = 15
        if nowy > self.prevy:
            event.delta = -sectionmoved
        elif nowy < self.prevy:
            event.delta = sectionmoved
        else:
            event.delta = 0
        self.prevy= nowy
        self.scrollposition += event.delta
        self.canvas.yview_moveto(self.scrollposition / self.canvasheight)

class TimeSlotItem(tk.Frame):
    """Time slot representing an hour with event count"""
    def __init__(self, parent, dateYMD, start_hour, event_count, first_event_idx):
        tk.Frame.__init__(self, parent, bg='gray20', relief=tk.RAISED, borderwidth=2, highlightthickness=0, width=550)

        self.first_event_idx = first_event_idx
        self.columnconfigure(0, weight=1)

        date_str = date.fromisoformat(dateYMD).strftime('%A, %B %d')
        # ('%I:%M %p - %A %B %d, %Y') for full timestamp formatting

        # Time range label
        self.time_label = tk.Label(self,
                                   text=f"{date_str} {start_hour:02d}:00 - {start_hour:02d}:59",
                                   font=('TkDefaultFont', 13, 'bold'),
                                   bg='gray20', fg='white', anchor='w',
                                   highlightthickness=0, borderwidth=0)
        self.time_label.grid(row=0, column=0, sticky='ew', padx=15, pady=(8, 2))

        # Event count label
        self.count_label = tk.Label(self,
                                    text=f"{event_count} event{'s' if event_count != 1 else ''}",
                                    font=('TkDefaultFont', 10),
                                    bg='gray20', fg='lightgray', anchor='w',
                                    highlightthickness=0, borderwidth=0)
        self.count_label.grid(row=1, column=0, sticky='ew', padx=15, pady=(0, 8))

        # Arrow indicator
        self.arrow_label = tk.Label(self, text="→",
                                    font=('TkDefaultFont', 18),
                                    bg='gray20', fg='chartreuse',
                                    highlightthickness=0, borderwidth=0)
        self.arrow_label.grid(row=0, column=1, rowspan=2, padx=15)

        # Bind click handlers ONLY to labels
        for widget in [self.time_label, self.count_label, self.arrow_label]:
            widget.bind('<Button-1>', self.on_click)

    def on_click(self, event=None):
        """Jump to first event in this time slot"""
        logger.debug(f"TimeSlot selected, jumping to event {self.first_event_idx}")
        app.eventIdx = self.first_event_idx
        app.select_event(self.first_event_idx)
        app.show_page(UserPage.PLAYER)

class EventListPage(tk.Canvas):
    """Full-screen scrollable event list organized by time slots"""
    def __init__(self, outpost_views):
        tk.Canvas.__init__(self, width=800, height=480, borderwidth=0,
                          highlightthickness=0, background="black")

        self.outpost_views = outpost_views
        self.current_view = None
        self.last_event_count = 0

        # Scrollable panel for time slots - full screen width minus button area
        list_width = 720
        slot_height = 75  # Height per time slot
        max_slots = 24    # 24 hours per day
        content_height = slot_height * max_slots
        visible_height = 480  # Full screen height
        self.event_panel = MenuPanel(self, list_width, content_height, show_scrollbar=True, visible_height=visible_height)
        self.event_panel.interior.columnconfigure(0, weight=1)  # Allow column to expand
        self.create_window(0, 0, window=self.event_panel, anchor=tk.NW)

        # Back button (top right)
        self.close_img = PIL.ImageTk.PhotoImage(file="images/close.png")
        id = self.create_image(730, 10, anchor="nw", image=self.close_img)
        self.tag_bind(id, "<Button-1>", lambda e: app.show_page(UserPage.PLAYER))

        # View title at top
        self.title_text = self.create_text(
            365, 30, text="Event List",
            fill='chartreuse', font=('TkDefaultFont', 14, 'bold')
        )

    def refresh_list(self, force=False):
        """Rebuild time-slot list for current view"""
        # Guard against no view selected
        if not app._current_view:
            return

        view = self.outpost_views[app._current_view]

        # Check if refresh needed
        if not force and self.current_view == app._current_view:
            if self.last_event_count == view.event_count():
                return

        self.current_view = app._current_view
        self.last_event_count = view.event_count()

        # Update title with total count
        total = view.event_count()
        self.itemconfig(self.title_text, text=f"{view.description} - {total} Events")

        # Clear existing items
        for child in self.event_panel.interior.winfo_children():
            child.destroy()

        # Build time slot list
        if view.event_count() > 0:
            # Group events by hour
            time_slots = self._group_events_by_date_and_hour(view.eventlist)

            # Build time slot list (newest first)
            for display_idx, (time_key, slot_data) in enumerate(
                    sorted(time_slots.items(), reverse=True)):
                dateYMD = time_key[0]
                start_hour = time_key[1]
                count = slot_data['count']
                first_idx = slot_data['first_idx']

                item = TimeSlotItem(self.event_panel.interior,
                                   dateYMD, start_hour, count, first_idx)
                # Don't use sticky='ew' - leave right side empty for scrolling
                item.grid(row=display_idx, column=0, sticky='w', padx=10, pady=6)
        else:
            no_events = tk.Label(self.event_panel.interior,
                                text="No events recorded",
                                font=('TkDefaultFont', 12),
                                bg='black', fg='gray')
            no_events.grid(row=0, column=0, padx=10, pady=50)

        # Update scroll region
        self.event_panel.interior.update_idletasks()
        bbox = self.event_panel.canvas.bbox("all")
        if bbox:
            self.event_panel.canvas.config(scrollregion=bbox)

    def _group_events_by_date_and_hour(self, eventlist):
        """Group events into hourly time slots"""
        slots = {}

        for idx, (timestamp, date, event_id, size) in enumerate(eventlist):
            # Extract hour from timestamp
            hour = timestamp.hour

            if (date, hour) not in slots:
                slots[(date, hour)] = {
                    'count': 0,
                    'first_idx': idx  # Index of first event in this hour
                }

            slots[(date, hour)]['count'] += 1

        return slots

class OutpostMenuitem(ttk.Frame):
    def __init__(self, parent, view, outpost_views):
        ttk.Frame.__init__(self, parent, borderwidth=0)
        v = outpost_views[view]
        self.image = convert_tkImage(v.thumbnail)
        self.label = tk.StringVar(value=v.menulabel)
        self.v = tk.Label(self, image=self.image, borderwidth=0, highlightthickness=0)
        self.t = ttk.Label(self, textvariable=self.label, font=('TkCaptionFont', 12),
                           background="black", foreground="chartreuse", justify="center")
        self.v.grid(column=0, row=0)
        self.t.grid(column=0, row=1)
        self.t.bind('<Button-1>', self.select_me, '+')
        self.outpost_views = outpost_views
        self.viewname = view
    def select_me(self, event=None):
        logger.debug(f"OutpostMenuitem {self.viewname} selected")
        app.select_outpost_view(self.viewname, True)
    def update(self) -> None:
        v = self.outpost_views[self.viewname]
        self.label.set(v.menulabel)
        self.image = convert_tkImage(v.thumbnail)
        self.v['image'] = self.image

class OutpostList(MenuPanel):
    def __init__(self, parent, width, height, outpost_views, visible_height=None):
        MenuPanel.__init__(self, parent, width, height, visible_height=visible_height)
        col, row = 0, 0
        for v in outpost_views:
            item = OutpostMenuitem(self.interior, v, outpost_views)
            outpost_views[v].store_menuref(item)
            item.grid(column=col, row=row, padx=10, pady=10)
            if col + 1 > 2:
                col = -1
                row += 1
            col += 1

class SettingsPage(tk.Canvas):
    def __init__(self):
        tk.Canvas.__init__(self, width=800, height=480, borderwidth=0, highlightthickness=0, background="black")

        # Title
        self.create_text(400, 20, text="Settings", fill="white",
                        font=('TkDefaultFont', 16, 'bold'))

        # Menu items as large touch-friendly buttons
        y_pos = 80
        button_height = 60
        button_spacing = 10

        # Motion Calibration button
        self.motion_cal_btn = tk.Button(
            self,
            text="Configure Motion Detector\nfor Current View",
            command=self.configure_motion,
            bg='#2E86AB', fg='white',
            font=('TkDefaultFont', 14, 'bold'),
            width=40, height=3,
            relief=tk.RAISED, bd=3
        )
        self.create_window(400, y_pos, window=self.motion_cal_btn, anchor="n")

        # Current view indicator (updates when views change)
        self.view_label = self.create_text(
            400, y_pos + button_height + 5,
            text="No view selected",
            fill="gray", font=('TkDefaultFont', 10, 'italic')
        )

        # Close/Back button
        self.close_img = PIL.ImageTk.PhotoImage(file="images/close.png")
        id = self.create_image(730, 10, anchor="nw", image=self.close_img)
        self.tag_bind(id, "<Button-1>", lambda e: app.show_page(UserPage.PLAYER))

        # Quit button
        self.quit_img = PIL.ImageTk.PhotoImage(file="images/quit.png")
        id = self.create_image(730, 80, anchor="nw", image=self.quit_img)
        self.tag_bind(id, "<Button-1>", quit)

    def configure_motion(self):
        """Start motion calibration for currently selected view"""
        if app.current_view:
            app.start_motion_calibration(app.current_view)
        else:
            # Show error message
            self.itemconfig(self.view_label,
                          text="Please select a view first (from Outposts page)",
                          fill="red")
            self.after(3000, lambda: self.update_view_label())

    def update_view_label(self):
        """Update the label showing which view is selected"""
        if app.current_view and app.current_view in app.outpost_views:
            view = app.outpost_views[app.current_view]
            text = f"Currently selected: {view.description} ({view.node})"
            self.itemconfig(self.view_label, text=text, fill="chartreuse")
        else:
            self.itemconfig(self.view_label,
                          text="No view selected", fill="gray")

class OutpostPage(tk.Canvas):
    # Provide room on the right for a non-scrolling region. This is a 3-across
    # presentation, allowing for padding in all directions around each item.
    # That plus an optional scrollbar should leave enough space on the far
    # right for a vertical button panel.
    # Currently assumes a (213, 160) sized thumbnail.
    def __init__(self, outpost_views):
        tk.Canvas.__init__(self, width=800, height=480, borderwidth=0, highlightthickness=0, background="black")
        list_width = 730
        item_height = 230
        item_count = len(outpost_views)
        list_height = item_height * ((item_count // 3) + 2)
        visible_height = 480  # Full screen height
        self.outpost_panel = OutpostList(self, list_width, list_height, outpost_views, visible_height=visible_height)
        self.create_window(0, 0, window=self.outpost_panel, anchor=tk.NW)
        self.close_img = PIL.ImageTk.PhotoImage(file="images/close.png")
        id = self.create_image(730, 10, anchor="nw", image=self.close_img)
        self.tag_bind(id, "<Button-1>", lambda e: app.show_page(UserPage.PLAYER))
        self.settings_img = PIL.ImageTk.PhotoImage(file="images/settings.png")
        id = self.create_image(730, 80, anchor="nw", image=self.settings_img)
        self.tag_bind(id, "<Button-1>", lambda e: app.show_page(UserPage.SETTINGS))

class PlayerPage(tk.Canvas):
    # Raspberry Pi 7-inch touch screen display: (800,480)
    def __init__(self, enable_share=False):
        tk.Canvas.__init__(self, width=800, height=480, borderwidth=0, highlightthickness=0, background="black")
        self.current_image = convert_tkImage(redx_image(800,480))
        self.pause_img = PIL.ImageTk.PhotoImage(file="images/pausebutton.png")
        self.play_img = PIL.ImageTk.PhotoImage(file="images/playbutton.png")
        self.prev_img = PIL.ImageTk.PhotoImage(file="images/prevbutton.png")
        self.next_img = PIL.ImageTk.PhotoImage(file="images/nextbutton.png")
        self.menu_img = PIL.ImageTk.PhotoImage(file="images/menubutton.png")
        self.share_img = PIL.ImageTk.PhotoImage(file="images/sharebutton.png")
        self.image = self.create_image(0, 0, anchor="nw", image=self.current_image)
        self.tag_bind(self.image, "<Button-1>", self.show_buttons)
        self.playpause = self.create_image(210, 60, anchor="nw", image=self.play_img)
        self.tag_bind(self.playpause, "<Button-1>", self.toggle)
        self.addtag_withtag('player_buttons', self.playpause)
        id = self.create_image(30, 330, anchor="nw", image=self.menu_img)
        self.tag_bind(id, "<Button-1>", self.menu)
        self.addtag_withtag('player_buttons', id)
        id = self.create_image(180, 330, anchor="nw", image=self.prev_img)
        self.tag_bind(id, "<Button-1>", self.prev)
        self.addtag_withtag('player_buttons', id)
        id = self.create_image(330, 330, anchor="nw", image=self.next_img)
        self.tag_bind(id, "<Button-1>", self.next)
        self.addtag_withtag('player_buttons', id)
        if enable_share:
            id = self.create_image(480, 330, anchor="nw", image=self.share_img)
            self.tag_bind(id, "<Button-1>", self.share)
            self.addtag_withtag('player_buttons', id)

        # Add LIST button (top right corner, larger for touch)
        self.list_button = self.create_rectangle(720, 10, 780, 70, fill='gray30', outline='white', width=2)
        self.list_text = self.create_text(750, 40, text='LIST', fill='white', font=('Arial', 10, 'bold'))
        self.tag_bind(self.list_button, "<Button-1>", self.show_event_list)
        self.tag_bind(self.list_text, "<Button-1>", self.show_event_list)
        self.addtag_withtag('player_buttons', self.list_button)
        self.addtag_withtag('player_buttons', self.list_text)

        self.toggle_pending = False
        self.auto_hide = None
        self.paused = True
        self.enable_share = enable_share
        self.progress_overlay = None
        self.progress_poll = None
        self.last_viewed_event = None

        self.hide_buttons()

    def update_image(self, image):
        #if image.shape[0] == 360: image = cv2.resize(image, (800, 450), interpolation=cv2.INTER_CUBIC)
        self.current_image = convert_tkImage(image)
        self.itemconfig(self.image, image=self.current_image)

    def show_buttons(self, event=None):
        if not self.toggle_pending:
            self.itemconfig('player_buttons', state='normal')
            if self.auto_hide is not None:
                self.after_cancel(self.auto_hide)
            self.auto_hide = self.after(2500, self.hide_buttons)

    def hide_buttons(self):
        self.itemconfig('player_buttons', state='hidden')

    def hide_buttons_now(self, event=None):
        self.after_cancel(self.auto_hide)
        self.hide_buttons()

    def update_state(self, is_paused):
        self.paused = is_paused
        if self.paused:
            self.itemconfig(self.playpause, image=self.play_img)
        else:
            self.itemconfig(self.playpause, image=self.pause_img)
        self.toggle_pending = False

    def toggle(self, event=None):
        logger.debug("PlayerPage play/pause button pressed")
        self.hide_buttons_now()
        self.play_pause()

    def pause(self):
        if not self.paused:
            self.play_pause()

    def play(self):
        if self.paused:
            self.play_pause()

    def play_pause(self):
        app.state_manager.request_transition(StateChange.TOGGLE, "PlayerPage.play_pause()")
        self.toggle_pending = True

    def menu(self, event=None):
        logger.debug("PlayerPage menu button pressed")
        self.pause()
        app.show_page(UserPage.OUTPOSTS)

    def prev(self, event=None):
        logger.debug("PlayerPage prev button pressed")
        self.hide_buttons_now()
        app.previous_event()

    def next(self, event=None):
        logger.debug("PlayerPage next button pressed")
        self.hide_buttons_now()
        app.next_event()

    def show_progress_overlay(self):
        """Display semi-transparent progress overlay"""
        if self.progress_overlay is None:
            # Create semi-transparent black overlay
            self.progress_overlay = self.create_rectangle(
                200, 150, 600, 330,
                fill='black', stipple='gray50', outline='white', width=2
            )
            self.progress_text = self.create_text(
                400, 200, text='Preparing export...',
                fill='white', font=('Arial', 14, 'bold')
            )
            self.progress_bar_bg = self.create_rectangle(
                220, 250, 580, 270, fill='#333333', outline='white'
            )
            self.progress_bar = self.create_rectangle(
                220, 250, 220, 270, fill='#00ff00', outline=''
            )
            self.addtag_withtag('progress_overlay', self.progress_overlay)
            self.addtag_withtag('progress_overlay', self.progress_text)
            self.addtag_withtag('progress_overlay', self.progress_bar_bg)
            self.addtag_withtag('progress_overlay', self.progress_bar)
        else:
            self.itemconfig('progress_overlay', state='normal')
            self.itemconfig(self.progress_text, text='Preparing export...')
            self.coords(self.progress_bar, 220, 250, 220, 270)

    def hide_progress_overlay(self):
        """Hide progress overlay"""
        if self.progress_overlay is not None:
            self.itemconfig('progress_overlay', state='hidden')

    def update_progress(self, phase, current, total):
        """Update progress overlay with current status"""
        if self.progress_overlay is not None:
            # Update text
            if total > 0:
                percent = int(100 * current / total)
                text = f"{phase}: {current}/{total} ({percent}%)"
            else:
                text = f"{phase}..."
            self.itemconfig(self.progress_text, text=text)

            # Update progress bar
            if total > 0:
                bar_width = 360 * (current / total)
                self.coords(self.progress_bar, 220, 250, 220 + bar_width, 270)

    def poll_export_progress(self):
        """Poll for export progress updates"""
        if app.video_exporter is None:
            self.hide_progress_overlay()
            return

        try:
            # Check for progress updates (non-blocking)
            while not app.video_exporter.progress_queue.empty():
                msg = app.video_exporter.progress_queue.get_nowait()

                if msg[0] == 'PHASE':
                    _, phase, current, total = msg
                    self.update_progress(phase, current, total)
                elif msg[0] == 'PROGRESS':
                    _, phase, current, total = msg
                    self.update_progress(phase, current, total)
                elif msg[0] == 'COMPLETE':
                    _, filename, event_count, frame_count = msg
                    text = f"Complete: {filename}\n{event_count} events, {frame_count} frames"
                    self.itemconfig(self.progress_text, text=text)
                    self.coords(self.progress_bar, 220, 250, 580, 270)
                    # Auto-hide after 3 seconds
                    self.after(3000, self.hide_progress_overlay)
                    return
                elif msg[0] == 'ERROR':
                    _, error_msg = msg
                    self.itemconfig(self.progress_text, text=f"Error: {error_msg}")
                    self.after(3000, self.hide_progress_overlay)
                    return

            # Continue polling
            self.progress_poll = self.after(200, self.poll_export_progress)

        except Exception as e:
            logger.exception(f"Error polling export progress: {str(e)}")
            self.hide_progress_overlay()

    def show_event_list(self, event=None):
        """Navigate to event list page"""
        logger.debug("PlayerPage LIST button pressed")
        self.pause()
        app.show_page(UserPage.EVENTS)

    def share(self, event=None):
        logger.debug("PlayerPage share button pressed")
        self.hide_buttons_now()
        if self.enable_share and app.state_manager.last_event is not None:
            viewname, date, evt_id = app.state_manager.last_event
            app.export_event(viewname, date, evt_id)
            self.show_progress_overlay()
            self.poll_export_progress()

class Application(ttk.Frame):
    def __init__(self, master=None):
        super().__init__(master)
        self.master = master
        self.grid(column=0, row=0)
        self.eventIdx = 0
        self.set_styling()
        self.winfo_toplevel().title("Sentinelcam Watchtower")
        self.alloc_ring_buffers()
        self.gather_view_definitions()
        self._current_view = None

        # Create the state management system
        self.state_manager = PlayerStateManager(self)

        # Thread data synchronization
        self.dataReady = threading.Event()
        self.newEvent = threading.Event()
        self.sourceCmds = queue.Queue()

        self.wirename = f"{SOCKDIR}/PlayerDaemon"
        self.player_daemon = PlayerDaemon(self.wirename, self._ringbuffers)
        self.sentinel_subscriber = SentinelSubscriber(CFG['sentinel'])
        self.eventList_updater = EventListUpdater(self.sentinel_subscriber.eventQueue, self.newEvent, self.outpost_views)

        # Initialize video exporter if configured
        self.video_exporter = None
        enable_share = False
        if 'video_export' in CFG:
            try:
                self.video_exporter = VideoExporter(CFG['video_export'], self.outpost_views)
                enable_share = True
                logger.info("VideoExporter initialized")
            except Exception as e:
                logger.error(f"Failed to initialize VideoExporter: {str(e)}")

        self.pages = [PlayerPage(enable_share=enable_share),
                      OutpostPage(self.outpost_views),
                      EventListPage(self.outpost_views),
                      SettingsPage(),
                      MotionCalibrationPage(self, self.outpost_views)]
        self.auto_play = False
        self.auto_pause = None
        self.move_next = False
        self.inactivity_timer = CFG.get('inactivity_timeout', 30)  # Default to 30 seconds
        self.player_panel = self.pages[UserPage.PLAYER]
        self.player_panel.grid(row=0, column=0)
        self.current_page = UserPage.PLAYER

        self.viewer = Player(self.dataReady, self.sourceCmds, self.wirename, self._rawBuffers, self.outpost_views, self.state_manager)

        self.master.bind_all('<Any-ButtonPress>', self.reset_inactivity)
        self.select_outpost_view(CFG['default_view'])
        self.update()

    def alloc_ring_buffers(self):
        ringmodel = CFG["ring_buffers"]
        ringsetups = [literal_eval(ring) for ring in ringmodel.values()]
        self._ringbuffers = {wh: RingBuffer(wh, l) for (wh, l) in ringsetups}
        self._rawBuffers = {wh: self._ringbuffers[wh].bufferList() for wh in self._ringbuffers}

    def gather_view_definitions(self):
        self.outpost_views = {}
        outpost_views = CFG['outpost_views']
        outposts = CFG['outposts']
        datapumps = CFG['datapumps']
        # setup the dictionary of outpost views
        for viewname in outpost_views:
            outpost_view = outpost_views[viewname]
            outpost = outposts[outpost_view['outpost']]
            viewdef = OutpostView(
                view = viewname,
                node = outpost_view['outpost'],
                publisher = outpost['image_publisher'],
                sinktag = outpost['datapump'],
                datapump = datapumps[outpost['datapump']],
                imgsize = literal_eval(outpost_view['size']),
                description = outpost_view['description']
            )
            self.outpost_views[viewname] = viewdef

    def set_styling(self):
        style = ttk.Style()
        style.configure("TFrame", background="black")

    def show_page(self, page):
        if page != self.current_page:
            # Pause calibration when switching away from it
            if self.current_page == UserPage.CALIBRATE:
                self.pages[UserPage.CALIBRATE].pause_calibration()

            # Resume calibration when switching back to it
            if page == UserPage.CALIBRATE:
                self.pages[UserPage.CALIBRATE].resume_calibration()

            # Refresh event list when navigating to it
            if page == UserPage.EVENTS:
                self.pages[UserPage.EVENTS].refresh_list(force=True)
            # Update settings page view label when showing settings
            if page == UserPage.SETTINGS:
                self.pages[UserPage.SETTINGS].update_view_label()

            self.pages[self.current_page].grid_remove()
            self.pages[page].grid(row=0, column=0)
            self.current_page = page

    @property
    def current_view(self):
        """Get the currently selected view name"""
        return self._current_view

    def select_outpost_view(self, viewname=None, auto_play=False):
        """Select a live outpost view"""
        if not viewname:
            viewname = self._current_view
        if viewname != self._current_view:
            self._current_view = viewname
        view = self.outpost_views[viewname]
        source_cmd = ((PlayerCommand.VIEWER, view.datapump, view.publisher, viewname), view.imgsize)
        self.state_manager.request_transition(StateChange.LOAD, "app.select_outpost_view()", source_cmd)
        self.auto_play = auto_play
        self.eventIdx = view.event_count()
        self.show_page(UserPage.PLAYER)
        self.view = view

    def select_event(self, idx, move_next=False):
        """Select a specific event from the event list for the current view"""
        self.move_next = move_next  # user pressed next button, assume auto-advance after event
        if move_next:
            logger.debug(f"Selecting event {idx} with auto-advance enabled")
            self.state_manager.request_transition(StateChange.AUTO, "app.select_event()", idx)
        (dt, date, event, size) = self.view.eventlist[idx]
        source_cmd = ((PlayerCommand.EVENT, self.view.datapump, self._current_view, date, event, size), size)
        self.state_manager.request_transition(StateChange.LOAD, "app.select_event()", source_cmd)
        self.auto_play = True

    def is_browsing_history(self):
        """Check if user is actively browsing event history"""
        return (self.state_manager.player_command and
                self.state_manager.player_command[0] == PlayerCommand.EVENT and
                self.state_manager.current_state != PlayerState.IDLE)

    def previous_event(self):
        if self.eventIdx > 0:
            self.eventIdx -= 1
            self.select_event(self.eventIdx)
        else:
            # TODO: No previous event, stay on current event
            self.select_event(self.eventIdx)

    def next_event(self):
        if self.eventIdx < self.view.event_count() - 1:
            self.eventIdx += 1
            self.select_event(self.eventIdx, move_next=True)
        else:
            # After current event found while stepping forward, switch to live view
            self.select_outpost_view(self._current_view, auto_play=True)

    def start_motion_calibration(self, viewname):
        """Enter motion calibration mode for a view"""
        self.pages[UserPage.CALIBRATE].start_calibration(viewname)
        self.show_page(UserPage.CALIBRATE)

    def export_event(self, viewname, date, event):
        """Queue an event for video export"""
        if self.video_exporter is not None:
            view = self.outpost_views[viewname]
            self.video_exporter.export_event(viewname, date, event, view.datapump)
            logger.info(f"Queued video export for {viewname} {date}/{event}")

    def reset_inactivity(self, event=None):
        if self.auto_pause is not None:
            self.master.after_cancel(self.auto_pause)
        self.auto_pause = self.master.after(self.inactivity_timer * 1000, self.reset_player)

    def reset_player(self):
        logger.debug("Forced pause and reset to PlayerPage for inactivity")
        self.select_outpost_view(self._current_view, auto_play=False)

    def update(self):
        _delay = 1

        # Process state machine transitions
        self.state_manager.process_transitions()

        # New image data is available from the player thread
        if self.dataReady.is_set():
            self.player_panel.update_image(self.viewer.get_imgdata())
            self.dataReady.clear()
            _delay += 1

        # New event data is available from the sentinel subscriber
        if self.newEvent.is_set():
            ((viewname, evtref), image) = self.eventList_updater.getEventData()
            logger.debug(f"Main update thread has new event data: {viewname} {evtref}")
            if viewname in self.outpost_views:
                v = self.outpost_views[viewname]
                v.update_thumbnail(image)
                v.add_event(evtref)
                v.menuref.update()
                # Only reset eventIdx if not actively browsing history
                if viewname == self._current_view and not self.is_browsing_history():
                    self.eventIdx = self.view.event_count()
                if self.state_manager.current_state in [PlayerState.PAUSED, PlayerState.READY, PlayerState.IDLE]:
                    self.select_outpost_view(viewname, auto_play=False)
                    self.player_panel.update_image(image)
            self.newEvent.clear()

        self.master.after(_delay, self.update)

def quit(event=None):
    root.destroy()

root = tk.Tk()
root.overrideredirect(True)
root.attributes("-fullscreen", True)
app = Application(master=root)
app.reset_inactivity()
app.mainloop()
