import time
from sentinelcam.utils import readConfig
from sentinelcam.tasklogic import MobileNetSSD, OpenCV_dnnFace

class Task:
    dataFeed = None
    # Function placeholders, defined by the taskHost
    def ringStart(self, frametime) -> int:
        return -1
    def ringNext(self) -> int:
        return -1
    def publish(self, msg, imageref=False) -> None:
        pass
    # Function prototypes, define these for task logic 
    def pipeline(self, frame) -> bool:
        # Return False to shutdown the pipeline and task
        return False 
    def finalize(self) -> None:
        pass

class PersonsFaces(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.jreq = jobreq
        self.evtData = trkdata
        self.dataFeed = feed
        self.start_time = time.time()
        self.detectFaces = cfg['faces'] 
        odCFG = cfg["mobilenetssd"]
        fdCFG = cfg["dnn_face"]
        if accelerator != 'ncs2':
            odCFG['target'] = 'cpu'
            fdCFG['target'] = 'cpu'
        self.od = MobileNetSSD(odCFG)
        self.fd = OpenCV_dnnFace(fdCFG)
        self.frame_cnt = 0
        self.person_cnt = 0
        self.face_cnt = 0
        time.sleep(1.001)  #  allow time for Intel NCS2 to become ready

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
        self.jreq = jobreq
        self.evtData = trkdata
        self.dataFeed = feed
        _cfg = cfg["mobilenetssd"]
        if accelerator != 'ncs2':
            _cfg['target'] = 'cpu'
        self.od = MobileNetSSD(_cfg)

    def pipeline(self, frame) -> bool:
        continuePipe = True
        (rects, labels) = self.od.detect(frame)
        if len(rects) > 0:
            detections = zip(labels, rects)
            for objs in detections:
                result = (objs[0], int(objs[1][0]), int(objs[1][1]), int(objs[1][2]), int(objs[1][3]))
                self.publish(result, True)
        return continuePipe

def TaskFactory(jobreq, trkdata, feed, cfgfile, accelerator) -> Task:
    menu = {
        'PersonsFaces'           : PersonsFaces,
        'MobileNetSSD_allFrames' : MobileNetSSD_allFrames
    }
    cfg = readConfig(cfgfile)
    task = menu[jobreq.jobTask](jobreq, trkdata, feed, cfg, accelerator)
    return task 
