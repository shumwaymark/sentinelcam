"""watchtower: Sentinelcam wall console, event and outpost viewer

Copyright (c) 2024 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import cv2
import numpy as np
import pandas as pd
import imagezmq
import zmq
import threading
import multiprocessing
from multiprocessing import sharedctypes
import tkinter as tk
from tkinter import ttk
import os
import queue
import msgpack
import simplejpeg
import PIL.Image, PIL.ImageTk
from ast import literal_eval
from time import sleep
from sentinelcam.datafeed import DataFeed
from sentinelcam.utils import FPS, readConfig
import traceback

CFG = readConfig(os.path.join(os.path.expanduser("~"), "watchtower.yaml"))
SOCKDIR = CFG["socket_dir"]

VIEWER = 1  # datapump,outpost,view
EVENT = 2   # datapump,date,event,imagesize

dataLock = threading.Lock()

def blank_image(w, h) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    return img

def redx_image(w, h) -> np.ndarray:
    img = blank_image(w, h)
    cv2.line(img, (0, 0), (w - 1, h - 1), (0, 0, 255), 4)
    cv2.line(img, (0, h - 1), (w - 1, 0), (0, 0, 255), 4)
    return img

REDX = redx_image(800, 480)

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
                self._data = imagedata
                self._data_ready.set()
        receiver.close()

    def close(self):
        self._stop = True

# multiprocessing class implementing a child subprocess for populating ring buffers of images 
class PlayerDaemon:
    def __init__(self, cmdq, wirename, ringbuffers):
        self.commandQ = cmdq 
        self.wirename = wirename
        self.ringbuffers = ringbuffers
        self.datafeeds = {}
        self.runswitch = multiprocessing.Value('i', 0)
        self.process = multiprocessing.Process(target=self._data_monster, args=(
            self.commandQ, self.runswitch, self.wirename, self.ringbuffers))
        self.process.start()

    def _setPump(self, pump) -> DataFeed:
        if not pump in self.datafeeds:
            self.datafeeds[pump] = DataFeed(pump)
        return self.datafeeds[pump]
        
    def _data_monster(self, commandQueue, keepgoing, wirename, ringbuffers):
        ringwire = RingWire(wirename)
        # Wait here for handshake from player thread in parent process
        handshake = ringwire.recv()  
        ringwire.send(handshake)  # acknowledge and get started
        frametimes = []
        frameidx = 0
        date, event, ring = None, None, None
        while True:
            cmd = commandQueue.get()
            if len(cmd) > 1 and cmd[0] in [VIEWER, EVENT]:
                # Connect to image source: outpost/view or datapump. 
                # Establish ringbuffer selection by image size.
                # Reset the buffer, then recv, send, read, and iterate.
                # Keep ring buffer populated until stopped or out of data.
                if cmd[0] == VIEWER:
                    (datapump, publisher, view) = cmd[1:]
                    # This taps into a live image publication stream. There is
                    # no end to this; it always represents current data capture.
                    # Just keep going here forever until explicity stopped. 
                    try:
                        receiver = ImageSubscriber(publisher, view)
                        frame = simplejpeg.decode_jpeg(receiver.receive()[1], colorspace='BGR')
                        wh = (frame.shape[1], frame.shape[0])
                        started = False
                        if ring != wh:  
                            ring = wh
                            ringbuffer = ringbuffers[ring]  # TODO: handle exception for unexpected sizes
                        ringbuffer.reset()
                        ringbuffer.put(frame)
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
                        receiver.close()
                    except Exception as ex:
                        print(f"ImageSubscriber failure reading from {publisher}, {str(ex)}")
                else:
                    (datapump, eventdate, eventid, imgsize) = cmd[1:]
                    # Unlike a live outpost viewer, datapump events have a definite end. When stepping backwards
                    # through a list of prior events, the player should pause and keep state (event and position).
                    # When stepping foward, with an assumed default auto-advance, reaching the end of a prior event
                    # should then seamlessly step into the next event and continue playing. This would transition 
                    # into live viewing mode when reaching the end of the most recent event.
                    feed = self._setPump(datapump)
                    if ring != imgsize:
                        ring = imgsize
                        ringbuffer = ringbuffers[ring]
                    if (eventdate, eventid) != (date, event):
                        (date, event) = (eventdate, eventid)
                        frametimes = feed.get_image_list(eventdate, eventid)
                        frameidx = 0
                        forward = True
                        ringbuffer.reset()
                    started = False
                    try:
                        while keepgoing.value:
                            if ringwire.ready():
                                msg = ringwire.recv() # response here reserved for player commands, reverse/forward/other
                                if not started:
                                    started = True
                                else:
                                    ringbuffer.frame_complete() 
                                ringwire.send(ringbuffer.get())
                            elif ringbuffer.isFull():
                                sleep(0.005)
                            else:
                                if (forward and frameidx < len(frametimes)) or (not forward and frameidx > -1): 
                                    jpeg = feed.get_image_jpg(eventdate, eventid, frametimes[frameidx])
                                    ringbuffer.put(simplejpeg.decode_jpeg(jpeg, colorspace='BGR'))
                                    frameidx = frameidx + 1 if forward else frameidx - 1
                                # Once the image supply has been exhausted, there will still be images waiting 
                                # in the ring buffer. Allow the partner process to deplete the buffer before ending
                                # the read loop.
                            if ringbuffer.isEmpty():
                                # Have reached the end, or the beginning, so just stop.
                                keepgoing.value = 0
                    except DataFeed.ImageSetEmpty as e:
                        ringbuffer.put(REDX)
                    except Exception as e:
                        print(f"Failure reading images from datapump, ({datapump},{eventdate},{eventid}): {str(e)}")

    def start(self, command_block):
        self.runswitch.value = 1
        self.commandQ.put(command_block)

    def stop(self):
        self.runswitch.value = 0

class TextHelper:
    def __init__(self) -> None:
        self._textColor = (0, 0, 0)
        self._lineType = cv2.LINE_AA
        self._textType = cv2.FONT_HERSHEY_SIMPLEX
        self._bboxColors = {}        
        self.setColors(['Unknown'])
    def setColors(self, names) -> None:
        for name in names:
            if name not in self._bboxColors:
                self._bboxColors[name] = tuple(int(x) for x in np.random.randint(256, size=3))
    def putText(self, frame, objid, text, x1, y1, x2, y2) -> None:
        cv2.rectangle(frame, (x1, y1), (x2, y2), self._bboxColors[objid], 2)
        cv2.rectangle(frame, (x1, (y1 - 28)), ((x1 + 160), y1), self._bboxColors[objid], cv2.FILLED)
        cv2.putText(frame, text, (x1 + 5, y1 - 10), self._textType, 0.5, self._textColor, 1, self._lineType)

class Player:
    def __init__(self, toggle, dataReady, srcQ, wirename, rawbuffers) -> None:
        self.setup_ringbuffers(rawbuffers)
        self.ringWire_connection(wirename)
        self.datafeeds = {}
        self._thread = threading.Thread(target=self._playerThread, args=(toggle, dataReady, srcQ))
        self._thread.daemon = True
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

    def _playerThread(self, toggle, dataReady, srcQ) -> None:
        paused = True
        viewfps = FPS()
        texthelper = TextHelper()
        outpost_view = None
        dataReady.clear()
        while True:
            datasource = srcQ.get()
            cmd = datasource[0]
            imgsize = datasource[1]
            # Player setup goes here...
            datafeed = self._setPump(cmd[1])
            ringbuffer = self.ringbuffers[imgsize]
            frametimes = []
            frameidx = 0
            if cmd[0] == EVENT:  
                # For events, send command on over to PlayerDaemon, and get it started now.
                # The node, view, and image size for any given event come from the date index.
                # Retrieve all tracking data and the list of image timestamps.
                app.player_daemon.stop() 
                app.player_daemon.start(cmd)
                (date, event, size) = cmd[2:]
                # TODO: Don't really need an index retrieval each 
                # time (usually has not changed) for same date.
                cwIndx = datafeed.get_date_index(date)
                evtSets = cwIndx.loc[cwIndx['event'] == event]
                if len(evtSets.index) > 0:
                    evt = evtSets.loc[evtSets['type']=='trk'].iloc[0]
                    node = evt.node
                    view = evt.viewname
                    time = evt.timestamp
                    size = (evt.width, evt.height)
                    evtTypes = [t for t in evtSets['type']]
                    try:
                        evtData = pd.concat([datafeed.get_tracking_data(date, event, t) for t in evtTypes])
                    except DataFeed.TrackingSetEmpty as e:
                        print(f"No tracking data for {e.date},{e.evt},{e.trk}")
                    evtData['name'] = evtData.apply(lambda x: str(x['classname']).split(':')[0], axis=1)
                    texthelper.setColors(evtData['name'].unique())
                    #evtData['proba'] = evtData.apply(lambda x: float(str(x['classname']).split()[1][:-1])/100, axis=1)
                    #evtData['usable'] = evtData.apply(lambda x: str(x['classname'])[-1:], axis=1)
                try:
                    frametimes = datafeed.get_image_list(date, event)
                except DataFeed.ImageSetEmpty as e:
                    app.player_panel.update_image(REDX)
                sleep(0.1)
            srcQ.task_done()
            while srcQ.empty():
                # Note that for any new command or pause/play toggle, an 
                # extended time period (hours, days, weeks) may have elapsed.
                if toggle.is_set():
                    paused = not paused
                    toggle.clear()
                    viewfps.reset()
                    if paused:
                        app.player_daemon.stop()
                    else:
                        app.player_daemon.start(cmd)
                        if cmd[0] == VIEWER:  
                            # The event list for a view can change while a live outpost viewer is actively
                            # running, so populate a current list of events for this view on each and every start.
                            outpost_view = cmd[3]
                            cwIndx = datafeed.get_date_index()
                            # All events for this view and date
                            viewEvts = cwIndx.loc[(cwIndx['type'] == 'trk') & (cwIndx['viewname'] == outpost_view)]
                            app.set_event_list([(rec.timestamp, rec.event, (rec.width, rec.height)) 
                                                for rec in viewEvts.itertuples()])
                        else:
                            pass  #  EVENT 
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
                                    for rec in evtData.loc[evtData['timestamp'] == frametimes[frameidx]].itertuples():
                                        if rec.name != 'Face':  # TODO: ignore fd1 only if face recon (fr1) data is present
                                            (x1, y1, x2, y2) = rec.rect_x1, rec.rect_y1, rec.rect_x2, rec.rect_y2
                                            texthelper.putText(image, rec.name, rec.classname, x1, y1, x2, y2)

                                    #  # draw timestamp on image frame
                                    #  tag = "{} UTC".format(frame_time.isoformat())
                                    #  cv2.putText(frame, tag, (30, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1)

                                    #  # whenever elapsed time within event > playback elapsed time,
                                    #  # estimate a sleep time to dial back the replay framerate
                                    #  playback_elaps = datetime.utcnow() - playback_begin
                                    #  if frame_elaps > playback_elaps:
                                    #      pause = frame_elaps - playback_elaps
                                    #      time.sleep(pause.seconds + pause.microseconds/1000000)

                                    frameidx += 1

                                self.set_imgdata(image)
                                dataReady.set()

                            else:
                                # player daemon will be in a stop() state for this condition
                                app.player_panel.play_pause()

                        except IndexError as ex:
                            print(f"IndexError cmd={cmd[0]} frameidx={frameidx} of {len(frametimes)}")
                        except Exception as ex:
                            print('Unhandled exception caught:', str(ex))
                            traceback.print_exc()                            

class Application(ttk.Frame):
    def __init__(self, master=None):
        super().__init__(master)
        self.master = master
        self.grid(column=0, row=0)
        self.winfo_toplevel().title("Sentinelcam Watchtower")
        self.alloc_ring_buffers()
        self.ringwire_setup()
        self.start_player_daemon()
        self.player_panel_setup()
        self.gather_sources()
        self.sourceCmds = queue.Queue()
        self.dataReady = threading.Event()
        self.toggle = threading.Event()
        self.toggle.clear()
        self.auto_pause = None
        self.evtList = []
        self.select_outpost_view('PiCamera')
        self.viewer = Player(self.toggle, self.dataReady, self.sourceCmds, self.wirename, self._rawBuffers)
        self.master.bind_all('<Any-ButtonPress>', self.reset_inactivity)
        self.update()

    def player_panel_setup(self):
        self.player_panel = PlayerPanel(root.winfo_screenwidth(), root.winfo_screenheight(), REDX)
        self.player_panel.grid(row=0, column=0)

    def alloc_ring_buffers(self):
        ringmodel = CFG["ring_buffers"]
        ringsetups = [literal_eval(ring) for ring in ringmodel.values()]
        self._ringbuffers = {wh: RingBuffer(wh, l) for (wh, l) in ringsetups}
        self._rawBuffers = {wh: self._ringbuffers[wh].bufferList() for wh in self._ringbuffers}

    def ringwire_setup(self):
        self.wirename = f"{SOCKDIR}/PlayerDaemon"

    def start_player_daemon(self):
        self.commandQueue = multiprocessing.Queue()
        self.player_daemon = PlayerDaemon(self.commandQueue, self.wirename, self._ringbuffers)

    def gather_sources(self):
        self.outpost_views = CFG['outpost_views']
        self.outposts = CFG['outposts']
        self.datapumps = CFG['datapumps']

    def select_outpost_view(self, viewname=None):
        if viewname is not None:
            self.current_view = viewname
            view = self.outpost_views[viewname]
            self.viewsize = literal_eval(view['size'])
            self.outpost = self.outposts[view['outpost']]
            self.datapump = self.datapumps[self.outpost['pump']]
        self.sourceCmds.put(((VIEWER, self.datapump, self.outpost['node'], self.current_view), self.viewsize))

    def set_event_list(self, newlist):
        with dataLock:
            self.evtList = newlist
            self.eventIdx = 0

    def select_event(self, idx):
        (dt, event, size) = self.evtList[idx]
        self.sourceCmds.put(((EVENT, self.datapump, dt.isoformat()[:10], event, size), size))

    def previous_event(self):
        if self.eventIdx < len(self.evtList):
            self.select_event(self.eventIdx)
            self.player_panel.play_if_paused()
            self.eventIdx += 1
        else:
            self.player_panel.update_image(REDX)

    def next_event(self):
        if self.eventIdx > 0:
            self.eventIdx -= 1
            self.select_event(self.eventIdx)
        else:
            self.sourceCmds.put(((VIEWER, self.datapump, self.outpost['node'], self.current_view), self.viewsize))
        self.player_panel.play_if_paused()

    def reset_inactivity(self, event=None):
        if self.auto_pause is not None:
            self.after_cancel(self.auto_pause)
        self.auto_pause = self.after(30000, self.player_panel.forced_pause)

    def update(self):
        _delay = 1
        if self.dataReady.is_set():
            self.player_panel.update_image(self.viewer.get_imgdata())
            self.dataReady.clear()
            _delay += 1
        self.master.after(_delay, self.update)

class PlayerPanel(tk.Canvas):
    def __init__(self, width, height, image):
        super().__init__(width=width, height=height, bg="black")
        self.current_image = convert_tkImage(image)
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
        self.paused = True
        self.show_buttons()

    def update_image(self, image):
        self.current_image = convert_tkImage(image)
        self.itemconfig(self.image, image=self.current_image)

    def show_buttons(self, event=None):
        self.itemconfig('player_buttons', state='normal')
        self.auto_hide = self.after(2500, self.hide_buttons)

    def hide_buttons(self):
        self.itemconfig('player_buttons', state='hidden')

    def hide_buttons_now(self, event=None):
        self.after_cancel(self.auto_hide)
        self.hide_buttons()

    def toggle(self, event=None):
        self.hide_buttons_now()
        self.play_pause()
    
    def play_if_paused(self):
        if self.paused:
            self.play_pause()

    def play_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.itemconfig(self.playpause, image=self.play_img)
        else:
            self.itemconfig(self.playpause, image=self.pause_img)
        app.toggle.set()

    def forced_pause(self):
        if not self.paused:
            self.play_pause()
        self.itemconfig('player_buttons', state='normal')
        app.select_outpost_view()

    def menu(self, event=None):
        self.hide_buttons_now()
        #print('menu button pressed')

    def prev(self, event=None):
        self.hide_buttons_now()
        app.previous_event()

    def next(self, event=None):
        self.hide_buttons_now()
        app.next_event()

    def share(self, event=None):
        self.hide_buttons_now()
        #print('share button pressed')

def quit(event=None):
     root.destroy()

root = tk.Tk()
root.overrideredirect(True)
root.attributes("-fullscreen", True)
app = Application(master=root)
app.after(1000, app.player_panel.play_pause())
app.reset_inactivity()
app.mainloop()
