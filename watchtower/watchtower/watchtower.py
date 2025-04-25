"""watchtower: Sentinelcam wall console, event and outpost viewer

Copyright (c) 2024 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import cv2
import numpy as np
import pandas as pd
import imutils
import json
import zmq
import threading
import queue
import traceback
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

CFG = readConfig(os.path.join(os.path.expanduser("~"), "watchtower.yaml"))
SOCKDIR = CFG["socket_dir"]

VIEWER = 1  # datapump,outpost,viewname
EVENT = 2   # datapump,viewname,date,event,imagesize

PLAYER_PAGE = 0    # Main page, outpost and event viewer
OUTPOST_PAGE = 1   # List of outpost views to choose from  
SETTINGS_PAGE = 2  # To be determined: settings, controls, and tools cafe

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
        self.stateQueue = multiprocessing.Queue()  # Queue for state change acknowledgments
        self.runswitch = multiprocessing.Value('i', 0)
        self.process = multiprocessing.Process(target=self._data_monster, args=(
            self.commandQueue, self.stateQueue, self.runswitch, self.wirename, self.ringbuffers))
        self.process.start()

    def _setPump(self, pump) -> DataFeed:
        if not pump in self.datafeeds:
            self.datafeeds[pump] = DataFeed(pump)
        return self.datafeeds[pump]
        
    def _data_monster(self, commandQueue, stateQueue, keepgoing, wirename, ringbuffers):
        ringwire = RingWire(wirename)
        # Wait here for handshake from player thread in parent process
        handshake = ringwire.recv()  
        ringwire.send(handshake)  # acknowledge and get started
        frametimes = []
        frameidx = 0
        date, event, ring, receiver = None, None, None, None
        print(f"PlayerDaemon subprocess started.")
        while True:
            cmd = commandQueue.get()
            # Connect to image source: outpost/view or datapump. 
            # Establish ringbuffer selection by image size.
            # Reset the buffer, then recv, send, read, and iterate.
            # Keep ring buffer populated until stopped or out of data.
            if cmd[0] == VIEWER:
                (datapump, publisher, view) = cmd[1:]
                # This taps into a live image publication stream. There is
                # no end to this; it always represents current data capture.
                # Just keep going here forever until explicitly stopped. 
                if not receiver:
                    receiver = ImageSubscriber(publisher, view)
                try:
                    receiver.subscribe(publisher, view)
                    receiver.start()
                    frame = simplejpeg.decode_jpeg(receiver.receive()[1], colorspace='BGR')
                    wh = (frame.shape[1], frame.shape[0])
                    started = False
                    if ring != wh:  
                        ring = wh
                        ringbuffer = ringbuffers[ring]  # TODO: handle exception for unexpected sizes
                    ringbuffer.reset()
                    ringbuffer.put(frame)
                    stateQueue.put(True)  # Acknowledge started
                    while keepgoing.value:
                        if ringwire.ready():
                            msg = ringwire.recv()
                            if not started:
                                started = True
                            else:
                                ringbuffer.frame_complete()
                            ringwire.send(ringbuffer.get())
                        elif ringbuffer.isFull():
                            sleep(0.005)
                        else:
                            ringbuffer.put(simplejpeg.decode_jpeg(receiver.receive()[1], colorspace='BGR'))
                    stateQueue.put(False)  # Acknowledge stopped
                except Exception as ex:
                    # TODO: need recovery / reattempt management here?
                    print(f"ImageSubscriber failure reading from {publisher}, {str(ex)}")
                finally:
                    receiver.stop()
            else:  # cmd[0] == EVENT
                (datapump, viewname, eventdate, eventid, imgsize) = cmd[1:]   
                # Unlike a live outpost viewer, datapump events have a definite end. Maintain state and keep
                # the ring buffer populated. An exhausted ring buffer is considered EOF when reading in either 
                # direction. Send an EOF response to the Player and reset to the beginning in either event.
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
                    started = False
                    stateQueue.put(True)  # Acknowledge started
                    while keepgoing.value:
                        if ringwire.ready():
                            msg = ringwire.recv() # response here reserved for player commands, reverse/forward/other
                            if not started:
                                started = True
                            else:
                                ringbuffer.frame_complete() 
                            if ringbuffer.isEmpty():
                                ringwire.send(-1)
                                forward = True
                                frameidx = 0
                            else:
                                ringwire.send(ringbuffer.get())
                        elif ringbuffer.isFull():
                            sleep(0.005)
                        else:
                            if (forward and frameidx < len(frametimes)) or (not forward and frameidx > -1): 
                                jpeg = feed.get_image_jpg(eventdate, eventid, frametimes[frameidx])
                                ringbuffer.put(simplejpeg.decode_jpeg(jpeg, colorspace='BGR'))
                                frameidx = frameidx + 1 if forward else frameidx - 1
                    stateQueue.put(False)  # Acknowledge stopped

                # TODO: Need to flood the ring bufffer with REDX images, and then allow for image retrieval
                # before exiting? Refactor this try/catch block appropriately if feasible, or seek alternative.
                # Perhaps implement recovery / reattempt management. 
                except KeyError as keyval:
                    print(f"PlayerDaemon internal key error '{keyval}'")
                    ringbuffer.put(redx_image(ring[0],ring[1]))
                except DataFeed.ImageSetEmpty as e:
                    ringbuffer.put(redx_image(ring[0],ring[1]))
                except TimeoutError:
                    ringbuffer.put(redx_image(ring[0],ring[1]))
                except Exception as e:
                    print(f"Failure reading images from datapump, ({datapump},{eventdate},{eventid}): {str(e)}")

    def start(self, command_block):
        self.runswitch.value = 1
        self.commandQueue.put(command_block)
        return self.stateQueue.get()  # Wait for acknowledgment

    def stop(self):
        self.runswitch.value = 0
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

class Player:
    def __init__(self, toggle, dataReady, srcQ, daemon_eof, player_daemon, wirename, rawbuffers, views) -> None:
        self.setup_ringbuffers(rawbuffers)
        self.ringWire_connection(wirename)
        self.texthelper = TextHelper()
        self.datafeeds = {}
        self.datafeed = None
        self.current_pump = None
        self.outpost_views = views
        self._thread = threading.Thread(daemon=True, target=self._playerThread, args=(toggle, dataReady, srcQ, daemon_eof, player_daemon))
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

    def get_bucket(self) -> int:
        self.ringWire.send(msgpack.packb(0))
        bucket = msgpack.unpackb(self.ringWire.recv())
        return bucket

    def set_imgdata(self, image) -> None:
        self.image = image

    def get_imgdata(self) -> np.ndarray:
        return (self.image)

    def _setPump(self, pump) -> DataFeed:
        if not pump in self.datafeeds:
            self.datafeeds[pump] = DataFeed(pump)
        return self.datafeeds[pump]
    
    def _gatherEventResults(self, date, event, datapump) -> tuple:
        if datapump != self.current_pump:
            self.datafeed = self._setPump(datapump)
            self.current_pump = datapump
        cwIndx = self.datafeed.get_date_index(date)  
        evtSets = cwIndx.loc[cwIndx['event'] == event]
        evtData = pd.DataFrame(columns=TRKCOLS)
        refsort = {'trk': 0, 'obj': 1, 'fd1': 2, 'fr1': 3}  # z-ordering for tracking result labels
        if len(evtSets.index) > 0:
            trkTypes = [t for t in evtSets['type']]
            try:
                evtData = pd.concat([self.datafeed.get_tracking_data(date, event, t) for t in trkTypes], 
                                    keys=[t for t in trkTypes], 
                                    names=['ref'])
                evtData['name'] = evtData.apply(lambda x: str(x['classname']).split(':')[0], axis=1)
                self.texthelper.setColors(evtData['name'].unique())
            except DataFeed.TrackingSetEmpty as e:
                print(f"No tracking data for {e.date},{e.evt},{e.trk}")
            except Exception as e: 
                print(f"Failure retrieving tracking data for {e.date},{e.evt},{e.trk}: {str(e)}")
        frametimes = []
        try:
            frametimes = self.datafeed.get_image_list(date, event)
        except DataFeed.ImageSetEmpty as e:
            print(f"No image data for {e.date},{e.evt}")
        except Exception as e: 
            print(f"Failure retrieving image list for {e.date},{e.evt}: {str(e)}")
        refresults = tuple(
            tuple(  
                (rec.name, rec.classname, rec.rect_x1, rec.rect_y1, rec.rect_x2, rec.rect_y2) 
                    for rec in evtData.loc[evtData['timestamp'] == frametime].sort_values(
                        by=['ref'], key=lambda x: x.map(refsort)).itertuples() 
            ) 
            for frametime in frametimes
        )
        return (frametimes, refresults)
        
    def _set_cache(self, view, evtkey, frametimes, refresults) -> None:
        with dataLock:
            self.outpost_views[view].eventCache[evtkey] = (frametimes, refresults)
    
    def _get_cache(self, view, evtkey) -> tuple:
        with dataLock:
            return self.outpost_views[view].eventCache.get(evtkey, ([], ()))

    def _playerThread(self, toggle, dataReady, srcQ, daemon_eof, player_daemon) -> None:
        paused = True
        viewfps = FPS()
        print(f"Player thread started.")
        while True:
            datasource = srcQ.get()
            cmd = datasource[0]
            imgsize = datasource[1]
            # Setup the Player for a new camera view/event
            ringbuffer = self.ringbuffers[imgsize]
            refresults = ()
            frametimes = []
            frameidx = 0
            forward = True
            if cmd[0] == EVENT:  
                (view, date, event, size) = cmd[2:]
                # For events, retrieve all tracking data and the list of image timestamps. First, 
                # apply a blur effect to the player display as visible feedback to the button press.
                self.set_imgdata(cv2.blur(image, (15, 15))) 
                dataReady.set()
                evtkey = (date, event)
                if evtkey in self.outpost_views[view].eventCache:
                    # If the event is already cached, use it. Otherwise, gather the event results and cache them.
                    (frametimes, refresults) = self._get_cache(view, evtkey)
                else:
                    (frametimes, refresults) = self._gatherEventResults(date, event, cmd[1])
                    self._set_cache(view, evtkey, frametimes, refresults)
                when = frametimes[0].strftime('%I:%M %p - %A %B %d, %Y') if len(frametimes) > 0 else ''
            else:
                view = cmd[3]
                when = 'current view'
            status_message = view + ' ' + when
            dataReady.clear()
            srcQ.task_done()
            while srcQ.empty():
                if toggle.is_set():
                    paused = not paused
                    viewfps.reset()
                    if paused:
                        player_daemon.stop()
                    else:
                        player_daemon.start(cmd)
                        if cmd[0] == EVENT:
                            event_start = frametimes[frameidx] if len(frametimes) > 0 else datetime.now()
                            playback_begin = datetime.now()
                    toggle.clear()
                if paused:
                    sleep(0.005)
                else:
                    if dataReady.is_set():
                        sleep(0.005)
                    else:
                        try:
                            bucket = self.get_bucket()
                            if bucket != -1:
                                image = ringbuffer[bucket]

                                if CFG['viewfps']:
                                    viewfps.update()
                                    text = "FPS: {:.2f}".format(viewfps.fps()) 
                                    cv2.putText(image, text, (10, image.shape[0]-10),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1)

                                if cmd[0] == EVENT:
                                    for (name, classname, x1, y1, x2, y2) in refresults[frameidx]:
                                        self.texthelper.putText(image, name, classname, x1, y1, x2, y2)

                                    if forward:
                                        # whenever elapsed time within event > playback elapsed time,
                                        # estimate a sleep time to dial back the replay framerate
                                        frame_elaps = frametimes[frameidx] - event_start
                                        playback_elaps = datetime.now() - playback_begin
                                        if frame_elaps > playback_elaps:
                                            pause = frame_elaps - playback_elaps
                                            time.sleep(pause.seconds + pause.microseconds/1000000)

                                    if frameidx < len(frametimes) - 1:
                                        frameidx += 1
                                else:
                                    frameidx += 1

                                if frameidx < 60:
                                    cv2.putText(image, status_message, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 1)

                                self.set_imgdata(image)
                                dataReady.set()

                            else:
                                daemon_eof.set()
                                frameidx = 0

                        except IndexError as ex:
                            print(f"IndexError cmd={cmd[0]} frameidx={frameidx} of {len(frametimes)}")
                        except Exception as ex:
                            print('Unhandled exception caught:', str(ex))
                            traceback.print_exc()                            

class SentinelSubscriber:
    def __init__(self, sentinel) -> None:
        self.eventQueue = multiprocessing.Queue()
        self.process = multiprocessing.Process(target=self._sentinel_reader, args=(
            sentinel, self.eventQueue))
        self.process.start()

    def _sentinel_reader(self, sentinel, eventQueue):
        # subscribe to Sentinel result publication
        sentinel_log = zmq.Context.instance().socket(zmq.SUB)
        sentinel_log.subscribe(b'')
        sentinel_log.connect(sentinel)
        event_lists = {}
        # consume every logging record published from the sentinel, watching for new events
        print("SentinelSubscriber started.")
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
                                    eventQueue.put((viewkey, evtkey))
                            else:
                                event_lists[viewkey] = [evtkey]
                                eventQueue.put((viewkey, evtkey))
                except (KeyError, ValueError):
                    pass
                except Exception:  
                    print(f"WatchTower exception parsing sentinel log '{message}'")

class EventListUpdater:
    def __init__(self, eventQ, newEvent, outpost_views):
        self._eventData = None
        self._image = blank_image(1,1)
        self._thread = threading.Thread(daemon=True, target=self._run, args=(eventQ, newEvent, outpost_views))
        self._thread.start()

    def getEventData(self) -> tuple:
        """Get the current event data and image"""
        return (self._eventData, self._image)

    def _run(self, eventQ, newEvent, outpost_views):
        day = str(date.today())
        receiver = None
        datafeeds = {}
        sink_events = {}
        # populate an initial event list for each view 
        for (sink, pump) in CFG['datapumps'].items():                
            if not pump in datafeeds:
                datafeeds[pump] = DataFeed(pump)
            feed = datafeeds[pump]
            try:
                cwIndx = feed.get_date_index(day).sort_values('timestamp')
                sink_events[sink] = cwIndx.loc[cwIndx['type']=='trk']
            except Exception as ex:
                print(f"EventListUpdater gather event list [{sink}]: {str(ex)}")
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
                    trkdata = feed.get_tracking_data(day, event)
                    persons = trkdata.loc[trkdata['classname'].str.startswith('person')]
                    sample_frame = len(persons.index) // 2
                    self._image = simplejpeg.decode_jpeg(
                        feed.get_image_jpg(day, event, persons.iloc[sample_frame]['timestamp']), 
                        colorspace='BGR')
                except Exception as ex:
                    print(f"EventListUpdater gather thumbnail for [{v.view}]: {str(ex)}")
                newEvent.set()
                while newEvent.is_set():
                    sleep(0.01)
        print('EventListUpdater started.')
        while True:
            (viewkey, evtkey) = eventQ.get()
            try:
                (node, view, sink) = viewkey
                (evtDate, event, pump) = evtkey
                if not pump in datafeeds:
                    datafeeds[pump] = DataFeed(pump)
                feed = datafeeds[pump]
                cwIndx = feed.get_date_index(evtDate)
                trkevt = cwIndx.loc[(cwIndx['event'] == event) & (cwIndx['type'] == 'trk')]
                if len(trkevt.index) > 0:
                    evtRec = trkevt.iloc[0] 
                    trkdata = feed.get_tracking_data(evtDate, event)
                    persons = trkdata.loc[trkdata['classname'].str.startswith('person')]
                    sample_frame = len(persons.index) // 2
                    evtRef = (evtRec.timestamp, evtDate, event, (evtRec.width, evtRec.height))
                    self._eventData = (view, evtRef)
                    self._image = simplejpeg.decode_jpeg(
                        feed.get_image_jpg(evtDate, event, persons.iloc[sample_frame]['timestamp']), 
                        colorspace='BGR')
                    newEvent.set()
                    while newEvent.is_set():
                        sleep(0.1)
            except Exception as e:
                print(f"EventListUpdater trapped exception: {str(e)}")

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
        self.max_events = CFG.get('max_events_per_view', 100)  # Default to 100 if not specified

    def store_menuref(self, menuitem) -> None:
        self.menuref = menuitem

    def event_count(self) -> int:
        with dataLock:
            return len(self.eventlist)

    def set_event_list(self, newlist) -> None:
        with dataLock:
            self.eventlist = newlist

    def add_event(self, event) -> None:
        with dataLock:
            if len(self.eventlist) >= self.max_events:
                # Remove cached data for the oldest event before removing it
                old_event = self.eventlist[0]
                event_key = (old_event[1], old_event[2])  # date_event key
                if event_key in self.eventCache:
                    del self.eventCache[event_key]
                # Remove oldest event when buffer is full
                self.eventlist.pop(0)
            self.eventlist.append(event)
            self.update_label()

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
    def __init__(self, parent, width, height, show_scrollbar=False):
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

        # reset the view
        canvas.xview_moveto(0)
        canvas.yview_moveto(0)
        # create a frame inside the canvas which will be scrolled with it
        self.interior = interior = ttk.Frame(canvas, height=self.canvasheight, borderwidth=0)
        interior_id = canvas.create_window(0, 0, window=interior, anchor=tk.NW)
        # update the scrollbars to match the size of the inner frame
        size = (width, height) # visible scrolling region
        canvas.config(scrollregion="0 0 %s %s" % size)
        # update the canvass width 
        canvas.config(width=self.canvaswidth)
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
        app.select_outpost_view(self.viewname, True)
    def update(self) -> None:
        v = self.outpost_views[self.viewname]
        self.label.set(v.menulabel)
        self.image = convert_tkImage(v.thumbnail)
        self.v['image'] = self.image

class OutpostList(MenuPanel):
    def __init__(self, parent, width, height, outpost_views):
        MenuPanel.__init__(self, parent, width, height)
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
        list_width = 730
        item_height = 50
        item_count = 0
        height = item_height * (item_count + 1)
        self.settings_panel = MenuPanel(self, list_width, height)
        self.create_window(0, 0, window=self.settings_panel, anchor=tk.NW)
        self.close_img = PIL.ImageTk.PhotoImage(file="images/close.png")
        id = self.create_image(730, 10, anchor="nw", image=self.close_img)
        self.tag_bind(id, "<Button-1>", lambda e: app.show_page(PLAYER_PAGE))
        self.quit_img = PIL.ImageTk.PhotoImage(file="images/quit.png")
        id = self.create_image(730, 80, anchor="nw", image=self.quit_img)
        self.tag_bind(id, "<Button-1>", quit)

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
        self.outpost_panel = OutpostList(self, list_width, list_height, outpost_views)
        self.create_window(0, 0, window=self.outpost_panel, anchor=tk.NW)
        self.close_img = PIL.ImageTk.PhotoImage(file="images/close.png")
        id = self.create_image(730, 10, anchor="nw", image=self.close_img)
        self.tag_bind(id, "<Button-1>", lambda e: app.show_page(PLAYER_PAGE))
        self.settings_img = PIL.ImageTk.PhotoImage(file="images/settings.png")
        id = self.create_image(730, 80, anchor="nw", image=self.settings_img)
        self.tag_bind(id, "<Button-1>", lambda e: app.show_page(SETTINGS_PAGE))

class PlayerPage(tk.Canvas):
    # Raspberry Pi 7-inch touch screen display: (800,480)
    def __init__(self):
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
        id = self.create_image(480, 330, anchor="nw", image=self.share_img)
        self.tag_bind(id, "<Button-1>", self.share)
        self.addtag_withtag('player_buttons', id)
        self.toggle_pending = False
        self.paused = True
        self.show_buttons()

    def update_image(self, image):
        #if image.shape[0] == 360: image = cv2.resize(image, (800, 450), interpolation=cv2.INTER_CUBIC)
        self.current_image = convert_tkImage(image)
        self.itemconfig(self.image, image=self.current_image)

    def show_buttons(self, event=None):
        if not self.toggle_pending:
            self.itemconfig('player_buttons', state='normal')
            self.auto_hide = self.after(2500, self.hide_buttons)

    def hide_buttons(self):
        self.itemconfig('player_buttons', state='hidden')

    def hide_buttons_now(self, event=None):
        self.after_cancel(self.auto_hide)
        self.hide_buttons()

    def update_state(self):
        self.paused = not self.paused
        if self.paused:
            self.itemconfig(self.playpause, image=self.play_img)
        else:
            self.itemconfig(self.playpause, image=self.pause_img)
        self.toggle_pending = False

    def toggle(self, event=None):
        self.hide_buttons_now()
        self.play_pause()

    def pause(self):
        if not self.paused:
            self.play_pause()
            self.after(100)  # Wait briefly to allow synchronization to propagate
   
    def play(self):
        if self.paused:
            self.play_pause()

    def play_pause(self):
        app.toggle.set()
        self.toggle_pending = True
        sleep(0.01)

    def forced_pause(self):
        self.pause()
        app.select_outpost_view()
        self.itemconfig('player_buttons', state='normal')

    def menu(self, event=None):
        self.pause()
        app.show_page(OUTPOST_PAGE)

    def prev(self, event=None):
        self.hide_buttons_now()
        app.previous_event()

    def next(self, event=None):
        self.hide_buttons_now()
        app.next_event()

    def share(self, event=None):
        self.hide_buttons_now()

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
        self.daemonEOF = threading.Event()
        self.dataReady = threading.Event()
        self.newEvent = threading.Event()
        self.toggle = threading.Event()
        self.sourceCmds = queue.Queue()
        self.wirename = f"{SOCKDIR}/PlayerDaemon"
        self.player_daemon = PlayerDaemon(self.wirename, self._ringbuffers)
        self.sentinel_subscriber = SentinelSubscriber(CFG['sentinel'])
        self.eventList_updater = EventListUpdater(self.sentinel_subscriber.eventQueue, self.newEvent, self.outpost_views)
        self.pages = [PlayerPage(), 
                      OutpostPage(self.outpost_views), 
                      SettingsPage()]
        self.auto_pause = None
        self.player_panel = self.pages[PLAYER_PAGE]
        self.player_panel.grid(row=0, column=0)
        self.current_page = PLAYER_PAGE
        self.viewer = Player(self.toggle, self.dataReady, self.sourceCmds, self.daemonEOF, 
                             self.player_daemon, self.wirename, self._rawBuffers, self.outpost_views)
        self.master.bind_all('<Any-ButtonPress>', self.reset_inactivity)
        self._should_resume = False  # Add new state variable
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
            self.pages[self.current_page].grid_remove()
            self.pages[page].grid(row=0, column=0)
            self.current_page = page

    def select_outpost_view(self, viewname=None, auto_play=False):
        """Select a live outpost view"""
        if not viewname: 
            viewname = self._current_view
        if viewname != self._current_view:
            self._current_view = viewname
        view = self.outpost_views[viewname]
        
        # First mark that we want to pause and wait for it to take effect
        self._should_resume = False
        self.player_panel.pause()
        # Wait for a short time to ensure pause takes effect
        self.master.after(100)
        
        # Now queue the new source command
        self.sourceCmds.put(((VIEWER, view.datapump, view.publisher, viewname), view.imgsize))
        self.eventIdx = view.event_count()
        self.show_page(PLAYER_PAGE)
        self.view = view
        
        # Set auto-play flag only after source command is queued
        self._should_resume = auto_play

    def select_event(self, idx):
        """
        Select an event to display and play it
        idx: The event index to select
        """
        (dt, date, event, size) = self.view.eventlist[idx]
        
        # First mark that we want to pause and wait for it to take effect
        self._should_resume = False
        self.player_panel.pause()
        # Wait for a short time to ensure pause takes effect
        self.master.after(100)
        
        # Now queue the new source command
        self.sourceCmds.put(((EVENT, self.view.datapump, self._current_view, date, event, size), size))
        
        # Set auto-play flag only after source command is queued
        self._should_resume = True

    def previous_event(self):
        if self.eventIdx > 0:
            self.eventIdx -= 1
            self.select_event(self.eventIdx)
        else:
            # When at start of events, stay on current event
            self.select_event(self.eventIdx) 

    def next_event(self):
        if self.eventIdx < self.view.event_count() - 1:
            self.eventIdx += 1
            self.select_event(self.eventIdx)
        else:
            # After current event found while stepping fowrard, switch to live view
            self.select_outpost_view(self._current_view, auto_play=True)

    def reset_inactivity(self, event=None):
        if self.auto_pause is not None:
            self.master.after_cancel(self.auto_pause)
        self.auto_pause = self.master.after(30000, self.player_panel.forced_pause)

    def update(self):
        _delay = 1
        if self.dataReady.is_set():
            self.player_panel.update_image(self.viewer.get_imgdata())
            self.dataReady.clear()
            # Check if we should resume playback after loading new event or view
            if self._should_resume:
                self.player_panel.play()
                self._should_resume = False
            _delay += 1
        if self.newEvent.is_set():
            ((viewname, evtref), image) = self.eventList_updater.getEventData()
            v = self.outpost_views[viewname]
            v.update_thumbnail(image)
            v.add_event(evtref)
            v.menuref.update()
            if viewname == self._current_view: self.eventIdx = self.view.event_count()
            self.newEvent.clear()
        if self.player_panel.toggle_pending:
            if not self.toggle.is_set():
                self.player_panel.update_state()
        if self.daemonEOF.is_set():
            self.player_panel.pause()
            self.daemonEOF.clear()
        self.master.after(_delay, self.update)

def quit(event=None):
    root.destroy()

root = tk.Tk()
root.overrideredirect(True)
root.attributes("-fullscreen", True)
app = Application(master=root)
app.reset_inactivity()
app.mainloop()
