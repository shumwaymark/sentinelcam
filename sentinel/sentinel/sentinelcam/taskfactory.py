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
    def publish(self, msg) -> None:
        pass
    # Function prototypes, define these for task logic 
    def pipeline(self, frame) -> bool:
        # Return False to shutdown the pipeline and task
        return False 
    def finalize(self) -> None:
        pass

class PersonsFaces(Task):
    def __init__(self, jobreq, trkdata, feed, cfg) -> None:
        self.jreq = jobreq
        self.evtData = trkdata
        self.dataFeed = feed
        self.start_time = time.time()
        self.cfg = cfg
        self.od = MobileNetSSD(cfg["mobilenetssd"])
        self.fd = OpenCV_dnnFace(cfg["dnn_face"])
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
        if p > self.person_cnt:
            self.person_cnt = p
        if p > 0 and self.cfg['faces']:
            faces = self.fd.detect(frame)
            f = len(faces)
            if f > self.face_cnt:
                self.face_cnt = f
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

def TaskFactory(jobreq, trkdata, feed, cfgfile) -> Task:
    menu = {
        'PersonsFaces' : PersonsFaces
    }
    cfg = readConfig(cfgfile)
    task = menu[jobreq.jobTask](jobreq, trkdata, feed, cfg)
    return task 
