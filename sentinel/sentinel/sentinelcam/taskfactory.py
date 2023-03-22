"""taskfactory: Defines task requests for the sentinel 

Copyright (c) 2023 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import time
import simplejpeg
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

class MobileNetSSD_allFrames(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.od = MobileNetSSD(cfg["mobilenetssd"], accelerator)

    def pipeline(self, frame) -> bool:
        (rects, labels) = self.od.detect(frame)
        if len(rects) > 0:
            detections = zip(labels, rects)
            for objs in detections:
                result = (objs[0], int(objs[1][0]), int(objs[1][1]), int(objs[1][2]), int(objs[1][3]))
                self.publish(result, True)
        return True  # process every frame 

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
            'PERSONS',
            str(self.jreq.eventDate),
            str(self.jreq.eventID),
            str(len(self.evtData.index)),
            str(self.person_cnt),
            str(self.face_cnt),
            str(self.frame_cnt),
            "{:.1f}".format(self.frame_cnt / (time.time() - self.start_time))  
        ])
        self.publish(stats)

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
                        str(len(trkData.index)),
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
                    str(len(trkData.index)),
                    str(person_cnt),
                    str(face_cnt),
                    str(limit),
                    str(frame_cnt),
                    "{:.1f}".format(frame_cnt / (time.time() - start_time))  
                ])
                self.publish(stats)

        return False

class CollectImageSizes(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:    
        self.startDate = jobreq.eventDate
        self.feed = feed

    def pipeline(self, frame) -> bool:
        # Get the complete list of available dates available through the DataFeed
        event_dates = self.feed.get_date_list()
        # ...and begin with the oldest date.
        event_dates.reverse()  
        for evtDate in event_dates:  
            if evtDate < self.startDate:
                continue
            dateTag = ('IMGSZ', evtDate)
            try:
                # Get the camwatcher event index for a given date
                cwIndx = self.feed.get_date_index(evtDate)
                if len(cwIndx.index) > 0:
                    # Process every event for this date
                    for _evt in cwIndx[:].itertuples():
                        event = _evt.event
                        node = _evt.node
                        view = _evt.viewname
                        imgs = self.feed.get_image_list(evtDate, event)
                        if len(imgs) > 0:
                            try:
                                jpeg = self.feed.get_image_jpg(evtDate, event, imgs[0])
                                if jpeg is not None:
                                    frame = simplejpeg.decode_jpeg(jpeg, colorspace='BGR')
                                    imgSize = (frame.shape[1], frame.shape[0])
                                    result = dateTag + (event, imgSize, node, view, len(imgs))
                                else:
                                    result = dateTag + (event, (-1,-1), node, view, len(imgs), "unable to retrieve image")
                            except Exception as e:
                                result = dateTag + (event, (-1,-1), node, view, len(imgs), str(e))
                        else:
                            result = dateTag + (event, (0,0), node, view, 0)
                        self.publish(result)
                else:
                    result = dateTag + ("ERROR", "camwatcher index is empty")
                    self.publish(result)
            except Exception as e:
                result = dateTag + ("ERROR", f"exception retrieving event data: {str(e)}")
                self.publish(result)
        return False

class MeasureRingLatency(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.jreq = jobreq
        self.dataFeed = feed
        self.od = MobileNetSSD(cfg["mobilenetssd"], accelerator)

    def pipeline(self, frame) -> bool:
        event_date = self.jreq.eventDate
        cwIndx = self.dataFeed.get_date_index(event_date)
        for cwEvt in cwIndx[:].itertuples():
            # For every event in the date...
            start_time = time.time()
            event = cwEvt.event
            eventKey = (event_date, event)
            event_start = cwEvt.timestamp
            bucket = self.ringStart(event_start, eventKey)
            ringbuff = self.getRing()
            frame_cnt, ring_wait, net_time = 0,0,0
            while bucket != -1:
                frame_cnt += 1
                _net_started = time.time()
                _nn = self.od.detect(ringbuff[bucket])
                _wait_started = time.time()
                bucket = self.ringNext()
                ring_wait += time.time() - _wait_started
                net_time += _wait_started - _net_started 
            elapsed = round(time.time() - start_time, 2)
            if frame_cnt > 0:
                result = ('RINGSTATS',
                          elapsed,                          # total_elapsed_time
                          frame_cnt,                        # frame_count
                          round(net_time,2),                # total_neuralnet_time
                          round(net_time / frame_cnt, 4),   # neuralnet_framerate 
                          round(ring_wait, 6),              # total_ring_latency
                          round(ring_wait / frame_cnt, 6),  # ringwait_per_frame
                          round(frame_cnt / elapsed, 2))    # frames_per_second 
                self.publish(result)
        return False

def TaskFactory(jobreq, trkdata, feed, cfgfile, accelerator) -> Task:
    menu = {
        'PersonsFaces'           : PersonsFaces,
        'MobileNetSSD_allFrames' : MobileNetSSD_allFrames,
        'DailyCleanup'           : DailyCleanup,
        'CollectImageSizes'      : CollectImageSizes,
        'MeasureRingLatency'     : MeasureRingLatency
    }
    cfg = readConfig(cfgfile)
    task = menu[jobreq.jobTask](jobreq, trkdata, feed, cfg, accelerator)
    return task 
