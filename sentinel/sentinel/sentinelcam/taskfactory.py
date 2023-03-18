"""taskfactory: Defines task requests for the sentinel 

Copyright (c) 2023 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import time
from sentinelcam.utils import readConfig
from sentinelcam.tasklibrary import MobileNetSSD, OpenCV_dnnFace

class Task:
    dataFeed = None
    # Function placeholders, defined by the taskHost
    def ringStart(self, frametime, newEvent=None) -> int:
        return -1
    def ringNext(self) -> int:
        return -1
    def getRing(self) -> list:
        return []
    def publish(self, msg, imageref=False) -> None:
        pass
    # Function prototypes, define these for task logic 
    def pipeline(self, frame) -> bool:
        # Return True to reiterate with the next frame 
        # Return False to shutdown the pipeline and task
        return False 
    def finalize(self) -> None:
        pass

class PersonsFaces(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.jreq = jobreq
        self.evtData = trkdata
        self.start_time = time.time()
        self.detectFaces = cfg['faces'] 
        self.od = MobileNetSSD(cfg["mobilenetssd"], accelerator)
        if self.detectFaces:
            self.fd = OpenCV_dnnFace(cfg["dnn_face"], accelerator)
        self.frame_cnt = 0
        self.person_cnt = 0
        self.face_cnt = 0

    def pipeline(self, frame) -> bool:
        continuePipe = True
        (rects, labels) = self.od.detect(frame)
        self.frame_cnt += 1
        p = 0
        for det in labels:
            if det.startswith("person"):
                p += 1
        self.person_cnt += p
        if p > 0 and self.detectFaces:
            faces = self.fd.detect(frame)
            self.face_cnt += len(faces)
        return continuePipe
                    
    def finalize(self) -> None:
        stats = ",".join([
            str(self.jreq.eventDate),
            str(self.jreq.eventID),
            str(len(self.evtData)),
            str(self.person_cnt),
            str(self.face_cnt),
            str(self.frame_cnt),
            "{:.1f}".format(self.frame_cnt / (time.time() - self.start_time))  
        ])
        self.publish(stats)

class MobileNetSSD_allFrames(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.od = MobileNetSSD(cfg["mobilenetssd"], accelerator)

    def pipeline(self, frame) -> bool:
        continuePipe = True
        (rects, labels) = self.od.detect(frame)
        if len(rects) > 0:
            detections = zip(labels, rects)
            for objs in detections:
                result = (objs[0], int(objs[1][0]), int(objs[1][1]), int(objs[1][2]), int(objs[1][3]))
                self.publish(result, True)
        return continuePipe

class DailyCleanup(Task):

    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.jreq = jobreq
        self.dataFeed = feed
        self.facePatience = cfg["face_patience"]
        if cfg["run_deletes"]:
            self.deleteOptions = cfg["delete_options"]
        else:
            self.deleteOptions = None
        self.od = MobileNetSSD(cfg["mobilenetssd"], accelerator)
        self.fd = OpenCV_dnnFace(cfg["dnn_face"], accelerator)

    def pipeline(self, frame) -> bool:
        # This is a one-shot pipeline to scan all events within the eventDate 
        event_date = self.jreq.eventDate
        cwIndx = self.dataFeed.get_date_index(event_date)
        for cwEvt in cwIndx[:].itertuples():
            start_time = time.time()
            event = cwEvt.event
            trkData = self.dataFeed.get_tracking_data(event_date, event)
            eventKey = (event_date, event)
            event_start = cwEvt.timestamp
            bucket = self.ringStart(event_start, eventKey)
            ringbuff = self.getRing()
            frame_cnt, person_cnt, face_cnt, limit = 0, 0, 0, 0
            while bucket != -1:
                (rects, labels) = self.od.detect(ringbuff[bucket])
                frame_cnt += 1
                p = 0
                for det in labels:
                    if det.startswith("person"):
                        p += 1
                if p > person_cnt:
                    person_cnt = p
                if p > 0:
                    limit += 1
                    faces = self.fd.detect(ringbuff[bucket])
                    f = len(faces)
                    if f > face_cnt:
                        face_cnt = f
                        break  # break out of the frame loop when first face found
                    elif limit > self.facePatience:
                        break  # still no face and have run out of patience looking
                bucket = self.ringNext()
            if self.deleteOptions is not None:
                delEvent = False
                if 'face_cnt' in self.deleteOptions: 
                    if face_cnt == self.deleteOptions['face_cnt']:
                        delEvent = True
                if "total_frame_threshold" in self.deleteOptions:
                    if bucket == -1 and frame_cnt < self.deleteOptions["total_frame_threshold"]:
                        delEvent = True
                if delEvent:
                    self.dataFeed.delete_event(event_date, event)
                    stats = ",".join([
                        'Delete',
                        str(event),
                        str(len(trkData)),
                        str(person_cnt),
                        str(face_cnt),
                        str(limit),
                        str(frame_cnt),
                        "{:.1f}".format(frame_cnt / (time.time() - start_time))  
                    ])
                    self.publish(stats)
            else:
                stats = ",".join([
                    str(event_date),
                    str(event),
                    str(len(trkData)),
                    str(person_cnt),
                    str(face_cnt),
                    str(limit),
                    str(frame_cnt),
                    "{:.1f}".format(frame_cnt / (time.time() - start_time))  
                ])
                self.publish(stats)

        return False

def TaskFactory(jobreq, trkdata, feed, cfgfile, accelerator) -> Task:
    menu = {
        'PersonsFaces'           : PersonsFaces,
        'MobileNetSSD_allFrames' : MobileNetSSD_allFrames,
        'DailyCleanup'           : DailyCleanup,
    }
    cfg = readConfig(cfgfile)
    task = menu[jobreq.jobTask](jobreq, trkdata, feed, cfg, accelerator)
    return task 
