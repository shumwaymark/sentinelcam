import cv2
import numpy as np
import time

class MobileNetSSD:
    def __init__(self, conf, accelerator="cpu") -> None:
        self.conf = conf  # configuration dictionary
        self.CLASSES = ["background", "aeroplane", "bicycle", "bird", "boat",
            "bottle", "bus", "car", "cat", "chair", "cow", "diningtable",
            "dog", "horse", "motorbike", "person", "pottedplant", "sheep",
            "sofa", "train", "tvmonitor"]
        (self.W, self.H) = (None, None)

        # load our serialized model from disk
        #print("Loading MobileNetSSD model...")
        self.net = cv2.dnn.readNetFromCaffe(self.conf["prototxt_path"],
	        self.conf["model_path"])

        if accelerator == "cpu":
            self.conf["target"] = "cpu"
        # check if the target processor is myriad, if so, then set the
        # preferable target to myriad
        if self.conf["target"] == "myriad":
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_MYRIAD)
            time.sleep(1.001)  #  allow time for Intel NCS2 to become ready?
        else:
            # set the preferable target processor to CPU and preferable
            # backend to OpenCV
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)

    def detect(self, frame) -> tuple:
        # initialize output lists
        objs = []
        labls = []
        
        # check to see if the frame dimensions are not set
        if self.W is None or self.H is None:
            (self.H, self.W) = frame.shape[:2]
        
        # convert the frame to a blob and pass the blob through the
        # network and obtain the detections
        blob = cv2.dnn.blobFromImage(frame, size=(300, 300), ddepth=cv2.CV_8U)
        self.net.setInput(blob, scalefactor=1.0/127.5, mean=[127.5,
            127.5, 127.5])
        detections = self.net.forward()
        
        # loop over the detections
        for i in np.arange(0, detections.shape[2]):
            # extract the confidence (i.e., probability) associated
            # with the prediction
            confidence = detections[0, 0, i, 2]

            # filter out weak detections by requiring a minimum
            # confidence
            if confidence > self.conf["confidence"]:
                # extract the index from the detections list
                idx = int(detections[0, 0, i, 1])
                # compute the (x, y)-coordinates of the bounding box
                # for the object
                box = detections[0, 0, i, 3:7] * np.array(
                    [self.W, self.H, self.W, self.H])
                objs.append(box.astype("int"))
                #objs.append((int(box[0]),int(box[1]),int(box[2]),int(box[3])))
                labls.append("{}: {:.4f}".format(
                    self.CLASSES[idx],
					confidence))

        return (objs, labls)

class OpenCV_dnnFace:
    def __init__(self, conf, accelerator="cpu") -> None:
        self.conf = conf  # configuration dictionary
        self.detector = cv2.dnn.readNetFromCaffe(
            self.conf["prototxt_path"],
	        self.conf["model_path"])

        if accelerator == "cpu":
            self.conf["target"] = "cpu"
        # check if the target processor is myriad, if so, then set the
        # preferable target to myriad
        if self.conf["target"] == "myriad":
            self.detector.setPreferableTarget(cv2.dnn.DNN_TARGET_MYRIAD)
            time.sleep(1.001)  #  allow time for Intel NCS2 to become ready?
        else:
            # set the preferable target processor to CPU and preferable
            # backend to OpenCV
            self.detector.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self.detector.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)

    def detect(self, frame) -> list:
        rects = []
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0,
            (300, 300), (104.0, 117.0, 123.0))
        self.detector.setInput(blob)
        faces = self.detector.forward()
        for i in range(faces.shape[2]):
            confidence = faces[0, 0, i, 2]
            if confidence > self.conf["confidence"]:
                box = faces[0, 0, i, 3:7] * np.array([w, h, w, h])
                rects.append(box.astype("int"))  # (x, y, x1, y1) 
        return rects
