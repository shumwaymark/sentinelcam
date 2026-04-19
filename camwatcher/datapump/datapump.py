import io
import os
import json
import logging
import logging.config
import pickle
import time
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
        pickle.dump(df, buffer)
        self.zmq_socket.send_json(md, flags | zmq.SNDMORE)
        return self.zmq_socket.send(buffer.getvalue(), flags, copy=copy, track=track)

    def pickle_and_send(self,
                        msg='OK',
                        obj=None,
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
        flags : int, optional
            zmq flags
        copy : bool, optional
            zmq copy flag
        track : bool, optional
            zmq track flag
        """

        md = dict(msg=msg, )
        p = pickle.dumps(obj)
        z = zlib.compress(p)
        self.zmq_socket.send_json(md, flags | zmq.SNDMORE)
        return self.zmq_socket.send(z, flags, copy=copy, track=track)

def create_tiny_jpeg() -> bytes:
    pixel = np.zeros((1, 1, 3), dtype=np.uint8)  # 1-pixel image
    buffer = simplejpeg.encode_jpeg(pixel)
    return buffer

def load_storage_report(sentinelcam_root):
    """Load pre-computed storage report from disk.

    Parameters
    ----------
    sentinelcam_root : str
        Base sentinelcam data path (e.g. /home/ops/sentinelcam)

    Returns
    -------
    dict or None
        The storage report dict, or None if unavailable
    """
    report_file = os.path.join(sentinelcam_root, 'storage_report', 'storage_report.pickle')
    if not os.path.isfile(report_file):
        return None
    try:
        with open(report_file, 'rb') as f:
            return pickle.load(f)
    except Exception:
        return None

class RequestMetrics:
    """Tracks request volume and response time for DataPump health reporting."""
    WINDOW = 100  # sliding window size for response time averaging

    def __init__(self):
        self.start_time = datetime.now()
        self.requests_served = 0
        self.requests_since_hc = 0
        self.last_request_time = None
        self._response_times = ([],[])  # sliding window of recent elapsed seconds for (images, others)
        self._req_start = None
        self._req_start_cmd = ''

    def begin(self, cmd=''):
        """Call at the top of each request, before processing."""
        self._req_start = time.monotonic()
        self._req_start_cmd = cmd

    def end(self):
        """Call after the response has been sent for every request."""
        self.requests_served += 1
        self.requests_since_hc += 1
        self.last_request_time = datetime.now()
        if self._req_start is not None:
            elapsed = time.monotonic() - self._req_start
            if self._req_start_cmd == 'pic':  # image request
                self._response_times[0].append(elapsed)
                if len(self._response_times[0]) > self.WINDOW:
                    self._response_times = (self._response_times[0][-self.WINDOW:], self._response_times[1])
            else:
                self._response_times[1].append(elapsed)
                if len(self._response_times[1]) > self.WINDOW:
                    self._response_times = (self._response_times[0], self._response_times[1][-self.WINDOW:])
            self._req_start = None

    def build_hc_response(self) -> dict:
        """Build the HC response payload and reset the since-last-HC counter."""
        now = datetime.now()
        uptime = now - self.start_time
        avg_ms = 0.0
        if self._response_times:
            avg_ms = round(sum(self._response_times[0] + self._response_times[1]) / (len(self._response_times[0]) + len(self._response_times[1])) * 1000, 3)
            avg_ms_img = round(sum(self._response_times[0]) / len(self._response_times[0]) * 1000, 3) if self._response_times[0] else 0.0
            avg_ms_other = round(sum(self._response_times[1]) / len(self._response_times[1]) * 1000, 3) if self._response_times[1] else 0.0
        result = {
            "flag": "HC",
            "component": "datapump",
            "timestamp": now.isoformat(),
            "uptime": str(uptime).split('.')[0],
            "requests_served": self.requests_served,
            "requests_since_last_hc": self.requests_since_hc,
            "avg_response_ms": avg_ms,
            "avg_response_ms_img": avg_ms_img,
            "avg_response_ms_other": avg_ms_other,
            "last_request": self.last_request_time.isoformat() if self.last_request_time else None
        }
        self.requests_since_hc = 0
        return result

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
    # Derive sentinelcam root from the configured data folder
    # datafolder is .../sentinelcam/camwatcher, root is one level up
    sentinelcam_root = os.path.dirname(CFG['datafolder'])
    log.info("datapump response loop starting")
    metrics = RequestMetrics()
    # TODO: Graceful shutdown / termination handling needed.
    # Need a policy for sending meaningful response codes back to the DataFeed.
    while True:
        msg = pump.zmq_socket.recv()
        try:
            request = msgpack.loads(msg)
        except Exception as e:
            log.error(f'Malformed request (msgpack decode failed): {e}')
            pump.send_reply(b'Error')
            continue
        reply = 'OK'
        if isinstance(request, dict) and 'cmd' in request:
            metrics.begin(request['cmd'])
            try:
                if request['cmd'] == 'dat':  # retrieve list of date folders
                    pump.pickle_and_send(reply, cData.get_date_list())
                    metrics.end()
                    continue
                elif request['cmd'] == 'idx':  # retrieve event index
                    cData.set_date(request['date'])
                    indx = cData.get_index()
                    pump.send_DataFrame(reply, indx)
                    metrics.end()
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
                    metrics.end()
                    continue
                elif request['cmd'] == 'img':  # retrieve list of image timestamps
                    cData.set_date(request['date'])
                    cData.set_event(request['evt'])
                    image_list = cData.get_event_images()
                    timestamps = [datetime.strptime(imageframe[-30:-4],"%Y-%m-%d_%H.%M.%S.%f")
                        for imageframe in image_list]
                    pump.pickle_and_send(reply, timestamps)
                    metrics.end()
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
                    metrics.end()
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
                        log.debug(f"camwatcher send request {camwatcher_control}")
                        camwatcher.send(json.dumps(camwatcher_control).encode('ascii'))
                        reply = camwatcher.recv()
                        log.debug(f"camwatcher delete response {reply}")
                elif request['cmd'] == 'HC':  # health check
                    reply = json.dumps(metrics.build_hc_response()).encode('ascii')
                elif request['cmd'] == 'hth':  # health summary for date
                    records = cData.get_health_summary(request['date'])
                    pump.pickle_and_send(reply, records)
                    metrics.end()
                    continue
                elif request['cmd'] == 'str':  # storage report
                    report = load_storage_report(sentinelcam_root)
                    if report is not None:
                        pump.pickle_and_send(reply, report)
                    else:
                        pump.pickle_and_send('NoReport', None)
                    metrics.end()
                    continue
                else:
                    log.error(f"Unrecognized command: {str(request)}")
                    reply = b'Error'
            except KeyError as keyval:
                log.error(f'Request field "{keyval}" missing for [{request["cmd"]}] command')
                # Send error in format matching what DataFeed expects for this command
                cmd = request.get('cmd', '')
                if cmd in ('idx', 'evt'):
                    pump.send_DataFrame('Error', pandas.DataFrame())
                elif cmd in ('dat', 'img', 'str', 'hth'):
                    pump.pickle_and_send('Error', None)
                elif cmd == 'pic':
                    pump.send_jpg('Error', tinyJPG)
                else:
                    pump.send_reply(b'Error')
                metrics.end()
                continue
            except Exception as e:
                log.exception(f'Unexpected exception [{request}] command: {str(e)}')
                cmd = request.get('cmd', '')
                if cmd in ('idx', 'evt'):
                    pump.send_DataFrame('Error', pandas.DataFrame())
                elif cmd in ('dat', 'img', 'str', 'hth'):
                    pump.pickle_and_send('Error', None)
                elif cmd == 'pic':
                    pump.send_jpg('Error', tinyJPG)
                else:
                    pump.send_reply(b'Exception')
                metrics.end()
                continue
        else:
            metrics.begin()
            log.error(f"Invalid request: {request}")
            reply = b'Error'
        metrics.end()
        pump.send_reply(reply)

if __name__ == "__main__":
    main()
