"""datafeed: A component of the SentinelCam data layer. 
Services data requests for access to camera event and image data.

Copyright (c) 2021 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import io
import pickle 
import zlib
import imagezmq
import msgpack
import pandas
from datetime import datetime
import logging
import queue
import threading
import time
import zmq

class DataFeed(imagezmq.ImageSender):

    def recv_jpg(self, copy=False):
        """Receives text msg, jpg buffer

        Parameters
        ----------
        copy : bool, optional
            zmq copy flag

        Returns
        -------
        msg 
            response code, text message, image name
        jpg_buffer
            bytestring jpg compressed image
        """

        msg, jpg_buffer = self.zmq_socket.recv_jpg(copy=copy)
        return msg, jpg_buffer
    
    def recv_DataFrame(self, flags=0, copy=False, track=False) -> "tuple[str, pandas.DataFrame]":
        """Receives text message and pickled pandas.DataFrame

        Parameters
        ----------
        flags : int, optional 
            zmq flags
        copy : bool, optional
            zmq copy flag
        track : bool, optional
            zmq track flag

        Returns
        -------
        str 
            response code / text message
        pandas.DataFrame
            response result
        """

        md = self.zmq_socket.recv_json(flags=flags)  # metadata text
        msg = self.zmq_socket.recv(flags=flags, copy=copy, track=track)
        payload = io.BytesIO(msg)
        return (md["msg"], pandas.read_pickle(payload))

    def recv_pickle(self, flags=0, copy=False, track=False):
        """Receives text message and compressed pickle 

        Parameters
        ----------
        flags : int, optional 
            zmq flags
        copy : bool, optional
            zmq copy flag
        track : bool, optional
            zmq track flag

        Returns
        -------
        str 
            response code / text message
        result
            unpickled payload
        """

        md = self.zmq_socket.recv_json(flags=flags)  # metadata text
        msg = self.zmq_socket.recv(flags=flags, copy=copy, track=track)
        payload = zlib.decompress(msg)
        return (md["msg"], pickle.loads(payload))

    def recv(self):
        return (None, self.zmq_socket.recv())

    #----------------------------------------------------------------------------------------

    class TrackingSetEmpty(Exception):
        def __init__(self, date, evt, trk):
            self.date = date
            self.evt = evt
            self.trk = trk

    class ImageSetEmpty(Exception):
        def __init__(self, date, evt):
            self.date = date
            self.evt = evt

    DATE_LST = 0
    DATE_IDX = 1
    TRK_DATA = 2
    IMG_LST = 3
    IMG_JPG = 4
    DEL_EVT = 5
    HEALTH = -1

    def __init__(self, connect_to, timeout=15.0):
        imagezmq.ImageSender.__init__(self, connect_to, REQ_REP=True)
        self._pump = connect_to
        self._timeout = timeout
        self._pumpResult = {
            DataFeed.DATE_LST: self.recv_pickle,
            DataFeed.DATE_IDX: self.recv_DataFrame,
            DataFeed.TRK_DATA: self.recv_DataFrame,
            DataFeed.IMG_LST: self.recv_pickle,
            DataFeed.IMG_JPG: self.recv_jpg,
            DataFeed.DEL_EVT: self.recv,
            DataFeed.HEALTH: self.recv
        }
        self._cmdQ = queue.Queue()
        self._haveResult = threading.Event()
        self._registerPoller()
        self._startThread()

    def _registerPoller(self) -> None:
        self._poller = zmq.Poller()
        self._poller.register(self.zmq_socket, zmq.POLLIN)
        
    def _startThread(self) -> None:
        self._thread = threading.Thread(target=self._cmdloop, args=())
        self._thread.daemon = True
        self._thread.start()

    def _haveResponse(self) -> bool:
        events = dict(self._poller.poll(0))
        if self.zmq_socket in events:
            return events[self.zmq_socket] == zmq.POLLIN
        else:
            return False    

    def _cmdloop(self):
        self._happy = True
        while self._happy:
            (cmd, request) = self._cmdQ.get()
            self.zmq_socket.send(msgpack.dumps(request))
            self._cmdQ.task_done()
            while not self._haveResult.is_set():
                if self._haveResponse():
                    (msg, result) = self._pumpResult[cmd]()
                    self._data = result
                    self._haveResult.set()
                else:
                    time.sleep(0.001)
                if not self._happy:
                    break

    def pump_action(self, cmd, request) -> object:
        self._haveResult.clear()
        self._cmdQ.put((cmd, request))
        flag = self._haveResult.wait(timeout=self._timeout)
        if not flag: # shutdown thread and attempt recovery
            self._happy = False
            timedout = f"Timed out reading from datapump {self._pump}"
            logging.error(timedout)
            self.zmq_socket.close()
            self.zmq_socket = self.zmq_context.socket(zmq.REQ)
            self.zmq_socket.connect(self._pump)
            self._registerPoller()
            self._startThread()            
            raise TimeoutError(timedout)
        return self._data

    def get_date_list(self) -> list:
        return self.pump_action(DataFeed.DATE_LST, {'cmd': 'dat'})

    def get_date_index(self, date=datetime.now().isoformat()[:10]) -> pandas.DataFrame:
        request = {'cmd': 'idx', 'date': date}
        return self.pump_action(DataFeed.DATE_IDX, request)

    def get_tracking_data(self, date, event, type='trk') -> pandas.DataFrame:
        request = {'cmd': 'evt', 'date': date, 'evt': event, 'trk': type}
        result = self.pump_action(DataFeed.TRK_DATA, request)
        if len(result.index) == 0: 
            raise DataFeed.TrackingSetEmpty(date, event, type)
        return result

    def get_image_list(self, date, event) -> list:
        request = {'cmd': 'img', 'date': date, 'evt': event}
        result = self.pump_action(DataFeed.IMG_LST, request)
        if len(result) == 0:
            raise DataFeed.ImageSetEmpty(date, event)
        return result

    def get_image_jpg(self, date, event, frametime) -> bytes:
        dt = frametime.isoformat()
        request = {'cmd': 'pic', 'date': date, 'evt': event,
                   'frametime': "{}_{}".format(dt[:10], dt[11:].replace(':','.'))}
        result = self.pump_action(DataFeed.IMG_JPG, request)
        return result

    def delete_event(self, date, event) -> str:
        request = {'cmd': 'del', 'date': date, 'evt': event}
        return self.pump_action(DataFeed.DEL_EVT, request)

    def health_check(self) -> str:
        req = {'cmd': 'HC'}
        self.zmq_socket.send(req)
        return self.zmq_socket.recv()

#----------------------------------------------------------------------------------------

class EventList:
    def __init__(self, feed, date1=datetime.now().isoformat()[:10], event=None, filename=None, date2=None, trk='trk') -> None:
        self.eventList = []
        if filename:
            with open(filename) as evtfile:
                self.eventList = [tuple(evtkey.split()[:2]) for evtkey in evtfile]
        else:
            if date2:
                datelist = [d for d in feed.get_date_list() if d >= date1 and d <= date2]
            else:
                datelist = [date1]
            for day in datelist:
                if event:
                    self.eventList.append((day, event))
                    break
                cwIndx = feed.get_date_index(day)
                event_set = cwIndx.loc[cwIndx['type'] == trk]['event'].to_list()
                for evt in event_set:
                    self.eventList.append((day, evt))

    def get_event_list(self):
        return self.eventList

# ----------------------------------------------------------------------------------------
#   See below for usaage 
# ----------------------------------------------------------------------------------------

if __name__ == "__main__":

    cfg = { 'datapump': 'tcp://data1:5556' } 
    today = datetime.now().isoformat()[:10]

    feed = DataFeed(cfg["datapump"])
    cindx = feed.get_date_index(today)

    # most recent 5 events
    for row in cindx[:5].itertuples():
        print(row.node + " " + row.viewname + " " + str(row.timestamp) + " " + row.event)
    
    lastevent = cindx.iloc[0].event
    print("Last event " + lastevent)

    evt_data = feed.get_tracking_data(today, lastevent)
    for row in evt_data[:10].itertuples():
        print(str(row.timestamp) + " " + 
              str(row.elapsed) + " " + 
              str(row.objid) + " " + 
              str(row.classname) + " " + 
              str(row.rect_x1) + " " + 
              str(row.rect_x2) + " " + 
              str(row.rect_y1) + " " + 
              str(row.rect_y2))

    frametimes = feed.get_image_list(today, lastevent)  # returns list of timestamps
    for frametime in frametimes[:10]:
        print(str(frametime))
