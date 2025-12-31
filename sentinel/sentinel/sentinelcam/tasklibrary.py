import os
import cv2
import imutils
import logging
import numpy as np
import PIL.Image
import time

# Building edgetpu support
# https://github.com/google-coral/pycoral/issues/85#issuecomment-2282225345

class MobileNetSSD:

    ENGINE_opencvDNN = 0
    ENGINE_edgetpu = 1

    def __init__(self, conf, accelerator="cpu") -> None:
        self.conf = conf  # configuration dictionary
        self.CLASSES = ["background", "aeroplane", "bicycle", "bird", "boat",
            "bottle", "bus", "car", "cat", "chair", "cow", "diningtable",
            "dog", "horse", "motorbike", "person", "pottedplant", "sheep",
            "sofa", "train", "tvmonitor"]

        logging.debug("Loading MobileNetSSD model")
        if accelerator == "coral":
            self.engine = MobileNetSSD.ENGINE_edgetpu
            from pycoral.utils.edgetpu import make_interpreter
            from pycoral.utils.dataset import read_label_file
            # create interpreter for EdgeTPU model
            self.net = make_interpreter(self.conf["edgetpu_model"])
            self.net.allocate_tensors()
            # load labels for model
            self.labels = read_label_file(self.conf["model_labels"])
            # import required modules for EdgeTPU inference
            from pycoral.adapters import common
            from pycoral.adapters import detect as edgetpu_detect
            # store imported modules for later use
            self.common = common
            self.edgetpu_detect = edgetpu_detect
        else:
            self.engine = MobileNetSSD.ENGINE_opencvDNN
            self.net = cv2.dnn.readNetFromCaffe(
                self.conf["prototxt_path"],
                self.conf["model_path"])

            if accelerator == "cpu":
                self.conf["target"] = "cpu"
            # set preferable target to myriad when requested
            if self.conf["target"] == "myriad":
                self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_MYRIAD)
                time.sleep(1.001)  #  allow time for Intel NCS2 to become ready?
            else:
                # set preferable target processor to CPU, preferable backend to OpenCV
                self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)

    def detect(self, frame) -> tuple:
        # initialize output lists
        objs = []
        labls = []

        if self.engine == MobileNetSSD.ENGINE_edgetpu:
            image = PIL.Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            # prepare the frame for object detection
            _, scale = self.common.set_resized_input(
                self.net, image.size, lambda size: image.resize(size, PIL.Image.LANCZOS))
            self.net.invoke()
            detections = self.edgetpu_detect.get_objects(self.net, self.conf["confidence"], scale)

            for detection in detections:
                # extract the bounding box coordinates
                bbox = detection.bbox
                objs.append((bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax))
                labls.append("{}: {:.4f}".format(
                    self.labels[detection.id],
                    detection.score))

        else:
            H, W = frame.shape[:2]
            # convert the frame to a blob and pass the blob through the
            # network and obtain the detections
            blob = cv2.dnn.blobFromImage(frame, size=(300, 300), ddepth=cv2.CV_8U)
            self.net.setInput(blob, scalefactor=1.0/127.5, mean=[127.5, 127.5, 127.5])
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
                    box = detections[0, 0, i, 3:7] * np.array([W, H, W, H])
                    objs.append(box.astype("int"))
                    #objs.append((int(box[0]),int(box[1]),int(box[2]),int(box[3])))
                    labls.append("{}: {:.4f}".format(
                        self.CLASSES[idx],
                        confidence))

        return (objs, labls)

class FaceDetector:

    ENGINE_opencvDNN = 0
    ENGINE_edgetpu = 1

    def __init__(self, conf, accelerator="cpu") -> None:
        self.conf = conf  # configuration dictionary
        if accelerator == "coral":
            self.engine = FaceDetector.ENGINE_edgetpu
            from pycoral.utils.edgetpu import make_interpreter
            # create interpreter for EdgeTPU model
            self.net = make_interpreter(self.conf["edgetpu_model"])
            self.net.allocate_tensors()
            # import required modules for EdgeTPU inference
            from pycoral.adapters import common
            from pycoral.adapters import detect as edgetpu_detect
            # store imported modules for later use
            self.common = common
            self.edgetpu_detect = edgetpu_detect
        else:
            self.engine = FaceDetector.ENGINE_opencvDNN
            self.detector = cv2.dnn.readNetFromCaffe(
                self.conf["prototxt_path"],
                self.conf["model_path"])
            if accelerator == "cpu":
                self.conf["target"] = "cpu"
            # set preferable target to myriad when requested
            if self.conf["target"] == "myriad":
                self.detector.setPreferableTarget(cv2.dnn.DNN_TARGET_MYRIAD)
                time.sleep(1.001)  #  allow time for Intel NCS2 to become ready?
            else:
                # set preferable target processor to CPU, preferable backend to OpenCV
                self.detector.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                self.detector.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)

    def detect(self, frame) -> list:
        rects, labls = [], []
        if self.engine == MobileNetSSD.ENGINE_edgetpu:
            # prepare the frame for object detection
            image = PIL.Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            _, scale = self.common.set_resized_input(
                self.net, image.size, lambda size: image.resize(size, PIL.Image.LANCZOS))
            self.net.invoke()
            detections = self.edgetpu_detect.get_objects(self.net, self.conf["confidence"], scale)
            for detection in detections:
                # extract the bounding box coordinates
                bbox = detection.bbox
                rects.append((bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax))
                labls.append("Face {}: {:.4f} [{},{}] ({}) {}".format(
                    detection.id,
                    detection.score,
                    bbox.width, bbox.height, bbox.area, bbox.valid))

        else:
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
        return (rects, labls)

def get_eyesHaarCascade(path=None):
    if path is None:
        path = ''
        opencv_home = cv2.__file__
        for folder in opencv_home.split(os.path.sep)[0:-1]:
            path += folder + os.path.sep
        eye_cascacde = os.path.join(path, 'data', 'haarcascade_eye.xml')
    else:
        eye_cascacde = path
    eye_detector = cv2.CascadeClassifier(eye_cascacde)
    return eye_detector

class FaceAligner:
    # Modified from original at https://pyimagesearch.com/2017/05/22/face-alignment-with-opencv-and-python/
    def __init__(self, cfg):
        self.config = cfg
        # store the facial landmark predictor, desired output left
        # eye position, and desired output face width + height
        self.eye_detector = get_eyesHaarCascade(cfg["haarcascade_path"])
        self.desiredLeftEye = cfg["desiredLeftEye"]
        self.desiredFaceWidth = cfg["desiredFaceWidth"]
        self.desiredFaceHeight = cfg["desiredFaceHeight"]
        # if the desired face height is None, set it to be the
        # desired face width (normal behavior)
        if self.desiredFaceHeight is None:
            self.desiredFaceHeight = self.desiredFaceWidth

    def landmarks(self, face) -> tuple:
        gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
        eyes = self.eye_detector.detectMultiScale(image=gray,
            scaleFactor=1.05, minNeighbors=3, minSize=(20,20), maxSize=(35,35))
        # establish eye centroids
        centerX = face.shape[1] // 2
        centerY = face.shape[0] // 2
        eyeCentroids = []
        for (x, y, w, h) in eyes:
            cX = int((x + x + w) / 2.0)
            cY = int((y + y + h) / 2.0)
            if cY > centerY * 1.05 or cY < centerY // 2:
                continue   # discard oblique perspectives and any false-positive detections
            eyeCentroids.append((cX, cY))

        angle = 0
        leftEyeCenter = (0,0)
        rightEyeCenter = (0,0)
        if len(eyeCentroids) == 2:
            if eyeCentroids[0][0] > eyeCentroids[1][0]:
                leftEyeCenter = eyeCentroids[0]
                rightEyeCenter = eyeCentroids[1]
            else:
                leftEyeCenter = eyeCentroids[1]
                rightEyeCenter = eyeCentroids[0]

        # compute the angle between the eye centroids
        dY = rightEyeCenter[1] - leftEyeCenter[1]
        dX = rightEyeCenter[0] - leftEyeCenter[0]
        angle = np.degrees(np.arctan2(dY, dX)) - 180

        # estimated focus metric as the variance of the Laplacian
        focus = cv2.Laplacian(gray, cv2.CV_64F).var()

        facemarks = (rightEyeCenter, leftEyeCenter, (dX, dY), angle, focus)
        return facemarks

    def assess(self, facemarks) -> bool:
        (rightEye, leftEye, distance, angle, focus) = facemarks
        relative_angle = 360 + angle if angle < -180 else abs(angle)
        candidate = (
            #  found 2 eyes?
            distance[0] != 0 and
            #  distance between left and right eye above cutoff?
            distance[0] < -20 and
            #  both eyes outside of a centered 20% prohibited area?
            leftEye[0] > ((self.desiredFaceWidth/2)+(self.desiredFaceWidth//10)) and
            rightEye[0] < ((self.desiredFaceWidth/2)-(self.desiredFaceWidth//10)) and
            #  for filtering out extreme alignment angles
            relative_angle < 17 and
            #  focus metric above a currently hard-coded, and low, threshold?
            focus > 50
        )
        return candidate

    def align(self, face, facemarks) -> None:
        (rightEyeCenter, leftEyeCenter, distance, angle, focus) = facemarks
        # compute the desired right eye x-coordinate based on the
        # desired x-coordinate of the left eye
        desiredRightEyeX = 1.0 - self.desiredLeftEye[0]

        # determine the scale of the new resulting image by taking
        # the ratio of the distance between eyes in the *current*
        # image to the ratio of distance between eyes in the
        # *desired* image
        (dX, dY) = distance
        dist = np.sqrt((dX ** 2) + (dY ** 2))
        desiredDist = (desiredRightEyeX - self.desiredLeftEye[0])
        desiredDist *= self.desiredFaceWidth
        scale = desiredDist / dist

        # compute center (x, y)-coordinates (i.e., the median point)
        # between the two eyes in the input image
        eyesCenter = (int((leftEyeCenter[0] + rightEyeCenter[0]) // 2),
            int((leftEyeCenter[1] + rightEyeCenter[1]) // 2))

        # grab the rotation matrix for rotating and scaling the face
        M = cv2.getRotationMatrix2D(eyesCenter, angle, scale)

        # update the translation component of the matrix
        tX = self.desiredFaceWidth * 0.5
        tY = self.desiredFaceHeight * self.desiredLeftEye[1]
        M[0, 2] += (tX - eyesCenter[0])
        M[1, 2] += (tY - eyesCenter[1])

        # apply the affine transformation
        (w, h) = (self.desiredFaceWidth, self.desiredFaceHeight)
        output = cv2.warpAffine(face, M, (w, h), flags=cv2.INTER_CUBIC)

        # return the aligned face
        return output

class OpenFace:
    def __init__(self, conf) -> None:
        self.embedder = cv2.dnn.readNetFromTorch(conf["model_path"])
        self.embedder.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)

    def detect(self, frame, box) -> np.ndarray:
        result = None
        (x, y, w, h) = [int(v) for v in box]
        # extract the face ROI and grab the ROI dimensions
        face = frame[y:y+h, x:x+w]
        (fH, fW) = face.shape[:2]
        # insure that face width and height are sufficiently large
        if fW < 20 or fH < 20:
            pass
        else:
            # construct a blob for the face ROI, then pass through the
            # embedding model to obtain the 128-d quantification of the face
            faceBlob = cv2.dnn.blobFromImage(face, 1.0 / 255,
                (96, 96), (0, 0, 0), swapRB=True, crop=False)
            self.embedder.setInput(faceBlob)
            vec = self.embedder.forward()
            result = vec.flatten()
        return result

def dhash(image, hashSize=8):
    # convert the image to grayscale and resize the grayscale image,
    # adding a single column (width) so we can compute the horizontal
    # gradient
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hashSize + 1, hashSize))
    # compute the (relative) horizontal gradient between adjacent
    # column pixels
    diff = resized[:, 1:] > resized[:, :-1]
    # convert the difference image to a hash and return it
    return sum([2 ** i for (i, v) in enumerate(diff.flatten()) if v])
