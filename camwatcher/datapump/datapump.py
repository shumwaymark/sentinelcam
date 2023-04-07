import io
import os
import logging
import logging.handlers
from datetime import datetime
from time import sleep
import pickle 
import queue
import subprocess
import threading
import traceback
import zlib
import zmq
import numpy as np
import imagezmq
import msgpack
import pandas
import simplejpeg
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

class BackgroundTasks:
    def __init__(self, tasks, csvdir, imgdir):
        self._tasks = tasks
        self._csvdir = csvdir
        self._imgdir = imgdir
        self._stop = False
        self._thread = threading.Thread(target=self._run, args=())
        self._thread.daemon = True
        self._thread.start()

    def _getDelCmds(self, event) -> list:
        cmdlist = []
        # delete event entry from date index
        cmdlist.append((False, ["sed", "-i", f"/{event[1]}/d", os.path.join(self._csvdir, event[0], 'camwatcher.csv')])) 
        # purge event data
        cmdlist.append((True, f"rm {os.path.join(self._csvdir, event[0], ''.join([event[1],'*']))}"))
        # purge captured image data
        cmdlist.append((True, f"ls {os.path.join(self._imgdir, event[0], ''.join([event[1],'*']))} | xargs rm"))
        return cmdlist
    
    def _run(self):
        logging.debug("Background Tasks thread started")
        while not self._stop:
            if self._tasks.empty():
                sleep(1)
                continue
            while not self._tasks.empty():
                (cmd, args) = self._tasks.get()
                logging.debug(f"background task '{cmd}': {args}")
                try:
                    if cmd == 'del':
                        for step in self._getDelCmds(args):
                            logging.debug(f"Event deletion: {step}")
                            result = subprocess.run(step[1], shell=step[0], capture_output=True, text=True)
                            if result.returncode != 0:
                                logging.error(f"Deletion error {result.returncode} for {args[0]}/{args[1]} on {step[0]}: {result.stderr}")
                except Exception as ex:
                    logging.error(f"Background Tasks unhandled exception: {str(ex)}")
                self._tasks.task_done()

    def close(self):
        self._stop = True
        self._thread.join()

def create_tiny_jpeg() -> bytes:
    pixel = np.zeros((1, 1, 3), dtype=np.uint8)  # 1-pixel image
    buffer = simplejpeg.encode_jpeg(pixel)
    return buffer

def main():
    start_logging()
    tinyJPG = create_tiny_jpeg()
    taskQueue = queue.Queue()
    bgTasks = BackgroundTasks(taskQueue, cfg['datafolder'], cfg['imagefolder'])
    cData = CamData(cfg['datafolder'], cfg['imagefolder'])
    pump = DataPump(f"tcp://*:{cfg['control_port']}")
    logging.info("datapump response loop starting")
    # TODO: Graceful shutdown / termination handling needed 
    # Need a policy for sending meaningful response codes back to the DataFeed
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
                    jpegfile = os.path.join(cfg['imagefolder'], request['date'],
                        request['evt'] + '_' + request['frametime'] + '.jpg')
                    if os.path.exists(jpegfile):
                        jpeg = open(jpegfile, "rb").read()
                    else:
                        jpeg = tinyJPG
                    pump.send_jpg(reply, jpeg)
                    continue
                elif request['cmd'] == 'del':  # delete event data
                    task = ('del', (request['date'], request['evt']))
                    taskQueue.put(task)
                    reply = b'OK'
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
            except Exception as e:
                logging.error(f'unexpected exception: {str(e)}')
                traceback.print_exc()
                reply = b'Error'
        else:
            logging.warning(f"Invalid request: {request}")
            reply = b'Error'
        pump.send_reply(reply) 

    bgTasks.close() 

def start_logging():
    log = logging.getLogger()
    handler = logging.handlers.RotatingFileHandler(cfg['logfile'],
        maxBytes=524288, backupCount=5)
    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.setLevel(logging.WARN)
    return log

if __name__ == "__main__":

    cfg = {'control_port': 5556, 
           'imagefolder' : '/mnt/usb1/sentinelcam/images',
           'datafolder'  : '/mnt/usb1/sentinelcam/camwatcher',
           'logfile'     : '/mnt/usb1/sentinelcam/logs/datapump.log'} 
    main()
