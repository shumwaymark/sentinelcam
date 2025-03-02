import io
import os
import json
import logging
import logging.config
import pickle 
import zlib
import zmq
import numpy as np
import imagezmq
import msgpack
import pandas
import simplejpeg
from datetime import datetime
from sentinelcam.camdata import CamData
from sentinelcam.facedata import FaceList
from sentinelcam.utils import readConfig

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

def create_tiny_jpeg() -> bytes:
    pixel = np.zeros((1, 1, 3), dtype=np.uint8)  # 1-pixel image
    buffer = simplejpeg.encode_jpeg(pixel)
    return buffer

def main():
    CFG = readConfig(os.path.join(os.path.expanduser("~"), "datapump.yaml"))
    logging.config.dictConfig(CFG['logconfig'])
    log = logging.getLogger()
    tinyJPG = create_tiny_jpeg()
    facelist = FaceList(CFG['facefile'])
    cData = CamData(CFG['datafolder'], CFG['imagefolder'])
    pump = DataPump(f"tcp://*:{CFG['control_port']}")
    camwatcher = zmq.Context.instance().socket(zmq.REQ)
    camwatcher.connect(CFG['camwatcher'])
    log.info("datapump response loop starting")
    # TODO: Graceful shutdown / termination handling needed. 
    # Need a policy for sending meaningful response codes back to the DataFeed.
    # TODO: Need disk and data analysis with clean-up and reporting as a nightly task.
    # Will need control panel instrumentation for this as well, including perhaps charts
    # of the storage breakdown, utilization, and available capacity of the data sink.
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
                    if 'trk' in request:
                        _trk = request['trk']
                    else:
                        _trk = 'trk'
                    evtData = cData.get_event_data(_trk)
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
                    jpegfile = os.path.join(CFG['imagefolder'], request['date'],
                        request['evt'] + '_' + request['frametime'] + '.jpg')
                    if os.path.exists(jpegfile):
                        jpeg = open(jpegfile, "rb").read()
                        if len(jpeg) == 0:
                            jpeg = tinyJPG
                    else:
                        jpeg = tinyJPG
                    pump.send_jpg(reply, jpeg)
                    continue
                elif request['cmd'] == 'del':  # delete event data
                    (date, event) = (request['date'], request['evt']) 
                    if facelist.event_locked(date, event):
                        reply = b'Locked'
                    else:
                        camwatcher_control = {}
                        camwatcher_control['cmd'] = 'DelEvt'
                        camwatcher_control['date'] = date
                        camwatcher_control['event'] = event
                        log.info(f"camwatcher send request {camwatcher_control}")
                        camwatcher.send(json.dumps(camwatcher_control).encode('ascii'))
                        reply = camwatcher.recv()
                        log.info(f"camwatcher delete response {reply}")
                elif request['cmd'] == 'HC':  # health checkcd 
                    reply = b'OK'
                else:
                    log.error(f"Unrecognized command: {str(request)}")
                    reply = b'Error'    
            except KeyError as keyval:
                log.error(f'Request field "{keyval}" missing for [{request["cmd"]}] command')
                reply = b'Error'
            except Exception as e:
                log.exception(f'Unexpected exception [{request}] command: {str(e)}')
                reply = b'Exception'
        else:
            log.error(f"Invalid request: {request}")
            reply = b'Error'
        pump.send_reply(reply)   # TypeError: not all arguments converted during string formatting

if __name__ == "__main__":
    main()
