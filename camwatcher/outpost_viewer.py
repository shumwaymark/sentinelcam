"""outpost_viewer.py -- Establish ImageZMQ JPEG image subscription, display in OpenCV """
import sys
import datetime
import traceback
import cv2
import imagezmq
import simplejpeg
import threading
from collections import deque

cfg = {'host': 'lab1.', 
       'port': 5567,
       'showfps': True} 

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

# Helper class implementing an IO deamon thread
class VideoStreamSubscriber:

    def __init__(self, hostname, port):
        self.hostname = hostname
        self.port = port
        self.velocity = FPS(200)
        self._stop = False
        self._data_ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=())
        self._thread.daemon = True
        self._thread.start()

    def receive(self, timeout=15.0):
        flag = self._data_ready.wait(timeout=timeout)
        if not flag:
            raise TimeoutError(
                "Timeout while reading from subscriber tcp://{}:{}".format(self.hostname, self.port))
        self._data_ready.clear()
        return self._data

    def _run(self):
        try:
            receiver = imagezmq.ImageHub("tcp://{}:{}".format(self.hostname, self.port), REQ_REP=False)
            while not self._stop:
                self._data = receiver.recv_jpg()
                self._data_ready.set()
                self.velocity.update()
            receiver.close()
        except Exception as ex:
            print("VideoStreamSubscriber failure: " + ex)

    def close(self):
        self._stop = True

if __name__ == "__main__":
    hostname = cfg["host"]
    port = cfg["port"]    
    tfps = FPS(200)
    color = (0,255,0)
    receiver = VideoStreamSubscriber(hostname, port) # Start image subscription thread
    try:
        while True:
            msg, frame = receiver.receive()
            #image = cv2.imdecode(np.frombuffer(frame, dtype='uint8'), -1)
            image = simplejpeg.decode_jpeg(frame, colorspace='BGR')
            if cfg["showfps"]:
                tfps.update()
                text = "FPS: {:.2f}/{:.2f}".format(receiver.velocity.fps(),tfps.fps()) 
                cv2.putText(image, text, (50, 450),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)            
            cv2.imshow(msg, image)
            cv2.waitKey(1)
    except (KeyboardInterrupt, SystemExit):
        print('Exit due to keyboard interrupt')
    except Exception as ex:
        print('Python error with no Exception handler:')
        print('Traceback error:', ex)
        traceback.print_exc()
    finally:
        receiver.close()
        sys.exit()
