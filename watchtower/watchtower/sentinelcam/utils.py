import os
import logging
import time
import threading
import imagezmq
from collections import deque
from datetime import datetime
from time import sleep
from typing import Tuple, Optional
import yaml
import zmq

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
class ImageSubscriber(imagezmq.ImageHub):
    def __init__(self, publisher, view):
        imagezmq.ImageHub.__init__(self, open_port=publisher, REQ_REP=False)
        self.publisher = publisher
        self.view = view
        self._stop = False
        self._data_ready = threading.Event()
        self._continue = threading.Event()
        self._data: Optional[Tuple[str, bytes]] = None
        self._connection_attempts = 0
        self._max_retries = 3
        self._setupPoller()
        self._startThread()

    def _setupPoller(self) -> None:
        self._poller = zmq.Poller()
        self._poller.register(self.zmq_socket, zmq.POLLIN)
        
    def _startThread(self) -> None:
        self._thread = threading.Thread(daemon=True, target=self._receiver, args=())
        self._thread.start()

    def msg_waiting(self) -> bool:
        events = dict(self._poller.poll(0))
        if self.zmq_socket in events:
            return events[self.zmq_socket] == zmq.POLLIN
        else:
            return False

    def _receiver(self):
        """Main receiver loop with connection management."""
        self._connection_attempts = 0  # Initialize outside both loops
        self._safe_disconnect()
        
        while True:
            self._continue.wait()
            while self._connection_attempts < self._max_retries:
                try:
                    self.connect(self.publisher)
                    self._stop = False
                    logging.info(f"Connected to publisher {self.publisher}")
                    
                    while not self._stop:
                        if self.msg_waiting():
                            try:
                                imagedata = self.recv_jpg()
                                msg = imagedata[0].split('|')
                                if msg[0].split(' ')[1] == self.view:
                                    self._data = (msg[2], imagedata[1])
                                    self._data_ready.set()
                            except zmq.error.ZMQError as e:
                                logging.error(f"ZMQ error while receiving: {e}")
                                break
                        else:
                            sleep(0.0005)
                    
                    # If we get here without error, reset attempts
                    self._connection_attempts = 0
                    break  # Exit retry loop on success
                    
                except zmq.error.ZMQError as e:
                    self._connection_attempts += 1
                    logging.error(f"Connection attempt {self._connection_attempts} failed: {e}")
                    if self._connection_attempts < self._max_retries:
                        sleep(1)  # Wait before retry
                        continue
                    logging.error("Max connection retries reached")
                    
                finally:
                    self._safe_disconnect()
            
            # Reset state before next iteration
            self._data_ready.clear()
            self._continue.clear()
            self._connection_attempts = 0  # Reset for next connection cycle

    def _safe_disconnect(self):
        """Safely disconnect from publisher."""
        try:
            self.zmq_socket.disconnect(self.publisher)
        except zmq.error.ZMQError as e:
            logging.debug(f"Disconnect error (expected if not connected): {e}")

    def receive(self, timeout=15.0):
        flag = self._data_ready.wait(timeout=timeout)
        if not flag:
            self.stop()            
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
