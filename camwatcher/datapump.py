import io
import os
import logging
import logging.handlers
import pickle 
import zlib
import zmq
import imagezmq
import msgpack
import pandas
from datetime import datetime
#from camwatcher.camdata import CamData
from camdata import CamData

class DataPump(imagezmq.ImageHub):
    """ Service access requests to camwatcher data store and Sentinel DataFeed 

    Resident as a daemon sub-process on an ImageHub node, servicing
    access requests to the camwatcher data store and saved images.

    Parameters
    ----------
    port : integer
        Port number for an ImageHub REP socket
    
    Methods
    -------
    send_jpg(resp, jpeg)
        Send response message and jpeg data
    send_DataFrame(msg, df)
        Pickle and send pandas.DataFrame with response message
    pickke_and_send(msg, x)
        Pickle, compress and send an object with response message
    """

    def send_jpg(self, resp, jpeg):
        """Sends jpg buffer, preceded 

        Parameters:
        -----------
        resp : str
            Response message, "OK" for success
        jpeg : buffer
            bytestring containing the jpg image to send 
        """
        self.zmq_socket.send_jpg(msg=resp, jpg_buffer=jpeg, copy=False)

    def send_DataFrame(self, 
                       msg='OK',
                       df=pandas.DataFrame(),
                       flags=0,
                       copy=False,
                       track=False):
        """Sends a pandas.DataFrame 

        Sends a pickled pandas.DataFrame as the response.
        Preceded by a response code or other text msg,

        Parameters:
        -----------
        msg : str 
            response code or message
        df : pandas.DataFrame 
            DataFrame to be pickled and sent in reply
        flags : int, optional 
            zmq flags
        copy : bool, optional
            zmq copy flag
        track : bool, optional
            zmq track flag
        """

        md = dict(msg=msg, )
        buffer = io.BytesIO()
        df.to_pickle(buffer)
        self.zmq_socket.send_json(md, flags | zmq.SNDMORE)
        return self.zmq_socket.send(buffer.getvalue(), flags, copy=copy, track=track)

    def pickle_and_send(self, 
                        msg='OK',
                        obj=None,
                        protocol=-1,
                        flags=0,
                        copy=False,
                        track=False):
        """Pickle and send  

        Pickle and compress an object to send as the response.
        Preceded by a response code or other text msg,

        Parameters:
        -----------
        msg : str 
            response code or message
        obj : data
            object to be sent 
        protocol : int, optional
            pickling protocol
        flags : int, optional 
            zmq flags
        copy : bool, optional
            zmq copy flag
        track : bool, optional
            zmq track flag
        """

        md = dict(msg=msg, )
        p = pickle.dumps(obj, protocol)
        z = zlib.compress(p)
        self.zmq_socket.send_json(md, flags | zmq.SNDMORE)
        return self.zmq_socket.send(z, flags, copy=copy, track=track)

def main():
    start_logging()
    cData = CamData(cfg['datafolder'], cfg['imagefolder'])
    pump = DataPump(f"tcp://*:{cfg['control_port']}")
    logging.info("datapump response loop starting")
    while True:  
        msg = pump.zmq_socket.recv()
        request = msgpack.loads(msg)
        reply = 'OK'
        if 'cmd' in request:
            try:
                if request['cmd'] == 'dat':  # retrieve list of date folders
                    pump.pickle_and_send(reply, cData.get_date_list())
                    continue
                elif request['cmd'] == 'idx':  # retrieve event index 
                    cData.set_date(request['date'])
                    indx = cData.get_index()
                    pump.send_DataFrame(reply, indx)
                    continue
                elif request['cmd'] == 'evt':  # retrieve event data 
                    cData.set_date(request['date'])
                    cData.set_event(request['evt'])
                    evtData = cData.get_event_data()
                    pump.send_DataFrame(reply, evtData)
                    continue
                elif request['cmd'] == 'img':  # retrieve list of image timestamps
                    cData.set_date(request['date'])
                    cData.set_event(request['evt'])
                    image_list = cData.get_event_images()
                    timestamps = [datetime.strptime(imageframe[-30:-4],"%Y-%m-%d_%H.%M.%S.%f") 
                        for imageframe in image_list]
                    pump.pickle_and_send(reply, timestamps)
                    continue
                elif request['cmd'] == 'pic':  # retrieve image frame 
                    jpegfile = os.path.join(cfg['imagefolder'], request['date'],
                        request['evt'] + '_' + request['frametime'] + '.jpg')
                    jpeg = open(jpegfile, "rb").read()
                    pump.send_jpg(reply, jpeg)
                    continue
                elif request['cmd'] == 'upd':  # event update processing placeholder
                    pass
                elif request['cmd'] == 'HC':  # health check
                    reply = b'OK'
                else:
                    logging.warning(f"Unrecognized command: {request}")
                    reply = b'Error'
            except KeyError:
                logging.warning(f"Malformed request: {request}")
                reply = b'Error'
        else:
            logging.warning(f"Invalid request: {request}")
            reply = b'Error'
        pump.send_reply(reply) 

def start_logging():
    log = logging.getLogger()
    handler = logging.handlers.RotatingFileHandler('datapump.log',
        maxBytes=1048576, backupCount=10)
    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    return log

if __name__ == "__main__":

    cfg = {'control_port': 5556, 
           'imagefolder': '/mnt/usb1/imagedata/video',
           'datafolder':  '/mnt/usb1/imagedata/camwatcher'} 
    main()
