import os
import datetime
from collections import deque
import yaml

class FPS:

	def __init__(self, history=160):  
		# default allows for 5 seconds of history at 32 images/sec 
		self._deque = deque(maxlen=history) 

	def update(self):
		# capture current timestamp
		self._deque.append(datetime.datetime.utcnow())
	
	def fps(self):
		# calculate and return estimated frames/sec
		if len(self._deque) < 2:
			return 0
		else:
			return (len(self._deque) / 
				(self._deque[-1] - self._deque[0]).total_seconds())

def readConfig(path):
	cfg = {}
	if os.path.exists(path):
		with open(path) as f:
			cfg = yaml.safe_load(f)
	return cfg
