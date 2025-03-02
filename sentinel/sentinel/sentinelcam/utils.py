import os
import time
import threading
import imagezmq
from collections import deque
from datetime import datetime
from time import sleep
import yaml

class FPS:
    def __init__(self, history=160) -> None:
        # default allows for 5 seconds of history at 32 images/sec   
        self._deque = deque(maxlen=history) 

    def update(self) -> None:
        # capture current timestamp
        self._deque.append(time.time())

    def reset(self) -> None:
        # restart the measurement
        self._deque.clear()

    def fps(self) -> float:
        # calculate and return current frames/sec
        if len(self._deque) < 2:
            return 0.0
        else:
            return (len(self._deque) / (self._deque[-1] - self._deque[0]))
    
    def get_min(self) -> int:
        # return minute from the last timestamp
        return time.localtime(self._deque[-1]).tm_min
        
    def lastStamp(self) -> datetime.timestamp:
         return datetime.fromtimestamp(self._deque[-1])
    
# Helper class implementing an IO deamon thread as an Outpost image subscriber
class ImageSubscriber:
    def __init__(self, publisher, view):
        self.publisher = publisher
        self.view = view
        self._stop = False
        self._data_ready = threading.Event()
        self._continue = threading.Event()
        self._thread = threading.Thread(daemon=True, target=self._run, args=())
        self._thread.start()

    def _run(self):
        receiver = imagezmq.ImageHub(self.publisher, REQ_REP=False)
        receiver.zmq_socket.disconnect(self.publisher)
        while True:
            self._continue.wait()
            receiver.connect(self.publisher)
            self._stop = False
            while not self._stop:
                imagedata = receiver.recv_jpg()
                msg = imagedata[0].split('|')
                if msg[0].split(' ')[1] == self.view:
                    self._data = (msg[2], imagedata[1])
                    self._data_ready.set()
            receiver.zmq_socket.disconnect(self.publisher)
            self._data_ready.clear()
            self._continue.clear()
            sleep(0.1)

    def receive(self, timeout=15.0):
        flag = self._data_ready.wait(timeout=timeout)
        if not flag:
            raise TimeoutError(f"Timed out reading from publisher {self.publisher}")
        self._data_ready.clear()
        return self._data

    def subscribe(self, publisher, view):
        self.publisher = publisher
        self.view = view

    def start(self):
        self._continue.set()

    def stop(self):
        self._stop = True

def readConfig(path):
	cfg = {}
	if os.path.exists(path):
		with open(path) as f:
			cfg = yaml.safe_load(f)
	return cfg
