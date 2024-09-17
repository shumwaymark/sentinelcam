import os
from time import time
from collections import deque
import yaml

class FPS:
    def __init__(self, history=160) -> None:  
        self._deque = deque(maxlen=history)  # default allows for 5 seconds of history at 32 images/sec 

    def update(self) -> None:
        # capture current timestamp
        self._deque.append(time())

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
        if len(self._deque) > 0:
            return time.localtime(self._deque[-1]).tm_min
        else:
            return None

def readConfig(path):
	cfg = {}
	if os.path.exists(path):
		with open(path) as f:
			cfg = yaml.safe_load(f)
	return cfg
