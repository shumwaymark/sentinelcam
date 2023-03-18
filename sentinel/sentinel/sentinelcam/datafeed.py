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

#def main():

    def get_date_index(self, date):
        req = msgpack.dumps({'cmd': 'idx', 'date': date})
        self.zmq_socket.send(req)
        (msg, df) = self.recv_DataFrame()
        return df

    def get_tracking_data(self, date, event):
        req = msgpack.dumps({'cmd': 'evt', 'date': date, 'evt': event})
        self.zmq_socket.send(req)
        (msg, df) = self.recv_DataFrame()
        return df

    def get_date_list(self):
        req = msgpack.dumps({'cmd': 'dat'})
        self.zmq_socket.send(req)
        (msg, result) = self.recv_pickle()
        return result

    def get_image_list(self, date, event):
        req = msgpack.dumps({'cmd': 'img', 'date': date, 'evt': event})
        self.zmq_socket.send(req)
        result = []
        try:
            (msg, result) = self.recv_pickle()
        except Exception as e:
            logging.error(f"DataFeed.get_image_list({date},{event}) exception {str(e)}")
        return result

    def get_image_jpg(self, date, event, frametime):
        dt = frametime.isoformat()
        req = msgpack.dumps({'cmd': 'pic', 'date': date, 'evt': event,
            'frametime': "{}_{}".format(dt[:10], dt[11:].replace(':','.'))})
        self.zmq_socket.send(req)
        (msg, img) = self.recv_jpg()
        return img

    def delete_event(self, date, event):
        req = msgpack.dumps({'cmd': 'del', 'date': date, 'evt': event})
        self.zmq_socket.send(req)
        return self.zmq_socket.recv()

    def health_check(self) -> str:
        req = msgpack.dumps({'cmd': 'HC'})
        self.zmq_socket.send(req)
        return self.zmq_socket.recv()

# ----------------------------------------------------------------------------------------
#   See below for usaage 
# ----------------------------------------------------------------------------------------

if __name__ == "__main__":

    cfg = { 'datapump': 'tcp://data1:5556' } 
    today = datetime.utcnow().isoformat()[:10]

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
