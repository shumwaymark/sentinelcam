"""taskfactory: Defines task requests for the sentinel 

Copyright (c) 2023 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import cv2
import h5py
import numpy as np
import pandas as pd 
import pickle
import time
import imutils
#import simplejpeg
from collections import namedtuple
from sentinelcam.utils import readConfig
from sentinelcam.datafeed import DataFeed
from sentinelcam.facedata import FaceBaselines, FaceList
from sentinelcam.tasklibrary import MobileNetSSD, FaceDetector, FaceAligner, OpenFace
from sentinelcam.tasklibrary import dhash

class Task:
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        pass
    # Function placeholders, defined by the taskHost
    def ringStart(self, frametime, newEvent=None, ringctrl='full') -> int:
        return -1
    def ringNext(self) -> int:
        return -1
    def getRing(self) -> list:
        return []
    def publish(self, msg, imageLogRef=None, cwUpd=False) -> None:
        pass
    # Function prototypes, define these for task logic 
    def pipeline(self, frame) -> bool:
        # Return True to reiterate with the next frame 
        # Return False to shutdown the pipeline and task
        return False 
    def finalize(self) -> None:
        # Optional, for implementing end of task finalization logic 
        pass

class MobileNetSSD_allFrames(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.od = MobileNetSSD(cfg["mobilenetssd"], accelerator)
        self.cwUpd = cfg["camwatcher_update"]
        self.refkey = cfg["trk_type"]

    def pipeline(self, frame) -> bool:
        (rects, labels) = self.od.detect(frame)
        if len(rects) > 0:
            detections = zip(labels, rects)
            for i, objs in enumerate(detections):
                result = (objs[0], i, int(objs[1][0]), int(objs[1][1]), int(objs[1][2]), int(objs[1][3]))
                self.publish(result, self.refkey, self.cwUpd)
        return True  # process every frame 

class GetFaces(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.fd = FaceDetector(cfg['dnn_face'], accelerator)
        self.cwUpd = cfg['camwatcher_update']
        self.refkey = cfg['trk_type']
        self.allFrames = cfg['all_frames']
        self.event_id = jobreq.eventID
        self.event_date = jobreq.eventDate
        self.imgs = feed.get_image_list(self.event_date, self.event_id)
        self.persons = trkdata.loc[trkdata['classname'].str.startswith('person')]
        if len(self.persons.index) > 0:
            # When processing a subset of frames, define a couple of iterators
            # to align image and tracking references within the event.
            self.cursor = iter(self.persons[:].itertuples()) 
            self.frames = iter(self.imgs)
            self.trkRec = next(self.cursor)
            self.frametime = next(self.frames)
        else:
            self.cursor = None
        self.face_cnt = 0
        self._search_cnt = 0

    def _publish_face_rects(self, faces) -> None:
        if len(faces) > 0:
            for i, face in enumerate(faces):
                result = ("Face", i, int(face[0]), int(face[1]), int(face[2]), int(face[3]))
                self.publish(result, self.refkey, self.cwUpd)
                self.face_cnt += 1

    def pipeline(self, frame) -> bool:
        if self.allFrames:
            faces = self.fd.detect(frame)
            if len(faces) > 0: self._publish_face_rects(faces)
            self._search_cnt += 1
        else:
            if not self.cursor:
                return False  # No 'persons' detected. Do not begin the search, end it.
            else:
                image = frame
                if self.frametime < self.trkRec.timestamp:
                    # Skip-ahead logic below. TODO: This becomes inefficient when advancing less
                    # than the lengh of the ring buffer, since the desired frame is already in there.
                    bucket = self.ringStart(self.trkRec.timestamp)
                    if bucket > -1:
                        image = self.getRing()[bucket]
                        try:
                            while self.frametime < self.trkRec.timestamp:
                                self.frametime = next(self.frames) 
                        except StopIteration:
                            return False   # Reached last image, end the task
                    else:
                        return False
                search_not_completed = True
                try:
                    while self.trkRec.timestamp <= self.frametime:
                        # There could be multiple persons detected in the image, so will have a 
                        # tracking record for each. Once face detection has executed for this frame,
                        # continue iterating through the tracker until next timestamp found.
                        if search_not_completed:
                            faces = self.fd.detect(image)  # finds every face in the image
                            if len(faces) > 0: self._publish_face_rects(faces)
                            search_not_completed = False
                            self._search_cnt += 1
                        self.trkRec = next(self.cursor)
                    # Internal tracking for timestamp of current frame.
                    self.frametime = next(self.frames)
                except StopIteration:
                    return False      # Reached last detected person, end the task.
        return True
    
    def finalize(self) -> None:
        results = ('Faces', self.event_date, self.event_id, len(self.imgs), len(self.persons.index), self._search_cnt, self.face_cnt)
        self.publish(results)

class FaceRecon(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.cwUpd = cfg['camwatcher_update']
        self.refkey = cfg['trk_type']
        self.trkrecs = iter(trkdata[:].itertuples())
        self.fa = FaceAligner(cfg["face_aligner"])
        self.fe = OpenFace(cfg["face_embeddings"])
        faceModel = pickle.loads(open(cfg['facemodel'], "rb").read())
        self.model = faceModel['svm']
        self.labels = faceModel['labels']
        self.fb = FaceBaselines(cfg['baselines'], self.labels.classes_)
        self.cnts = [0 for i in range(len(self.labels.classes_))]
        self.cnts.append(0)  # one more on the end for the Unknown class
        self.unk = len(self.cnts) - 1
        self.facecnt = len(trkdata.index)
        self.event_id = jobreq.eventID
        self.event_date = jobreq.eventDate
        if self.facecnt > 0:
            self.trkRec = next(self.trkrecs)
            self.frametime = self.trkRec.timestamp 
        else:
            self.frametime = None

    def pipeline(self, frame) -> bool:
        while self.trkRec.timestamp <= self.frametime:
            x1, y1, x2, y2 = self.trkRec.rect_x1, self.trkRec.rect_y1, self.trkRec.rect_x2, self.trkRec.rect_y2
            if x1<0:x1=0
            if y1<0:y1=0
            face = frame[y1:y2, x1:x2]
            if len(face) == 0: return True 
            if face.shape[1] < 96: face = imutils.resize(face, width=96, inter=cv2.INTER_CUBIC)
            facemarks = self.fa.landmarks(face)
            candidate = self.fa.assess(facemarks)
            if candidate:
                validate = self.fa.align(face, facemarks)
            else:
                validate = face  
            v = validate.shape
            embeddings = self.fe.detect(validate, (0,0,v[1],v[0]))
            # perform classification to recognize the face
            preds = self.model.predict_proba(embeddings.reshape(1,-1))
            j = np.argmax(preds)
            proba = preds[0,j]
            name = self.labels.classes_[j]
            if proba > 0.97:
                (distance, margin) = self.fb.compare(embeddings, j)
                if distance > 0.99:
                    # almost certainly someone else
                    (k, distance) = self.fb.search(embeddings)
                    margin = distance - self.fb.thresholds()[k]
                    if k != j:
                        proba = 0
                        if distance > 0.99:
                            name, j = 'Unknown', self.unk
                        else:
                            name, j = self.labels.classes_[k], k
            else:
                (k, distance) = self.fb.search(embeddings)
                margin = distance - self.fb.thresholds()[k]
                if k != j:
                    proba = 0
                    if distance > 0.99:
                        name, j = 'Unknown', self.unk
                    else:
                        name, j = self.labels.classes_[k], k
            if margin < 0.05:  
                # TODO: Parameterize (or improve) this. Always consider these as possible candidates 
                # for inclusion in recognition model, since distance within fudge factor over threshold.
                candidate = True
            flag = '*' if candidate else ''
            if candidate or name != 'Unknown':
                classlabel = "{}: {:.2f}% {}".format(name, proba * 100, flag)
                result = (classlabel, self.trkRec.objid, x1, y1, x2, y2)
                self.publish(result, self.refkey, self.cwUpd)
                self.cnts[j] += 1
            try:
                self.trkRec = next(self.trkrecs)
            except StopIteration:
                return False 
        self.frametime = self.trkRec.timestamp 
        return True
    
    def finalize(self) -> None:
        namelist = [self.labels.classes_[n] for n in range(len(self.labels.classes_))]
        namelist.append('Unknown')
        cnts = ", ".join([f"{namelist[n]} {self.cnts[n]}" for n in range(len(self.cnts)) if self.cnts[n]>0])
        results = ('Recon', self.event_date, self.event_id, self.facecnt, cnts)
        self.publish(results)

class FaceSweep(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.feed = feed
        self.taskDate = jobreq.eventDate
        self.ref_type = cfg['ref_type']
        #self.threshold = cfg['threshold']
        self.facelist = FaceList(cfg['facefile'])
        self.fa = FaceAligner(cfg["face_aligner"])

    def pipeline(self, frame) -> False:  # runs once
        # Sweep for new candidates 
        fldlist = ['date','event','timestamp','objid','source','status','name','proba','dist','margin',
                    'x1','y1','x2','y2','rx','ry','lx','ly','dx','dy','angle','focus']
        facerec = namedtuple('facerec', fldlist)
        refkeys = [self.facelist.format_refkey(r) for r in self.facelist.get_fullset()[:].itertuples()]
        new_faces = 0
        prev_hash = 0
        cwIndx = self.feed.get_date_index(self.taskDate)
        # TODO: protect existing selections from being inadvertently over-written with new/duplicated data.
        # Probably OK to permit this if not included in the subset with non-zero status flags. But should first
        # remove any exsiting content already held for that event. 
        for sweepchk in cwIndx.loc[cwIndx['type'] == self.ref_type].itertuples():
            trkdata = self.feed.get_tracking_data(self.taskDate, sweepchk.event, self.ref_type)
            trkdata['name'] = trkdata.apply(lambda x: str(x['classname']).split(':')[0], axis=1)
            trkdata['proba'] = trkdata.apply(lambda x: float(str(x['classname']).split()[1][:-1])/100, axis=1)
            trkdata['usable'] = trkdata.apply(lambda x: str(x['classname'])[-1:], axis=1)
            for consider in trkdata.loc[trkdata['usable'] == '*'].itertuples():
                image = cv2.imdecode(np.frombuffer(
                    self.feed.get_image_jpg(self.taskDate, sweepchk.event, consider.timestamp), 
                    dtype='uint8'), -1)                    
                x1, y1, x2, y2 = consider.rect_x1, consider.rect_y1, consider.rect_x2, consider.rect_y2
                if x1<0:x1=0
                if y1<0:y1=0
                face = image[y1:y2, x1:x2]                    
                if len(face) > 0:
                    hash = dhash(face)
                    if hash != prev_hash:
                        if face.shape[1] < 96: face = imutils.resize(face, width=96, inter=cv2.INTER_CUBIC)
                        ((rx,ry), (lx,ly), (dx,dy), angle, focus) = self.fa.landmarks(face)
                        r = {'date': self.taskDate,
                             'event': sweepchk.event,
                             'timestamp': consider.timestamp,
                             'objid': consider.objid,
                             'source': 0,
                             'status': 0,
                             'name': consider.name,
                             'proba': round(consider.proba, 4),
                             'dist': 0,                             # TODO: collect this also, somehow
                             'margin': 0,                           # TODO: collect this also, somehow
                             'x1': x1,
                             'y1': y1,
                             'x2': x2,
                             'y2': y2,
                             'rx': rx,
                             'ry': ry,
                             'lx': lx,
                             'ly': ly,
                             'dx': dx,
                             'dy': dy,
                             'angle': round(angle, 1),
                             'focus': round(focus, 2)
                        }
                        keytest = self.facelist.format_refkey(facerec(**r))
                        if keytest not in refkeys:
                            self.facelist.add_rows(pd.DataFrame(r.values(), index=fldlist).T)
                            new_faces += 1
                    prev_hash = hash 
        # Should always push any updates back to data sink. SFTP? 
        if new_faces:
            self.facelist.commit()
            self.publish(f'FaceSweep: {new_faces} face candidates added from {self.taskDate}.')
        return False

class FaceDataUpdate(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.feed = feed
        self.taskDate = jobreq.eventDate
        self.facedata = cfg['facedata']
        self.facelist = FaceList(cfg['facefile'])
        self.fa = FaceAligner(cfg["face_aligner"])
        self.fe = OpenFace(cfg["face_embeddings"])

    def pipeline(self, frame) -> False:  # runs once
        # Sweep for new selections to be included in recognition model
        update_cnt = 0
        updates = self.facelist.get_selections()
        if len(updates.index) > 0:
            with h5py.File(self.facedata, 'a') as hdf5:
                for r in updates[:].itertuples():
                    image = cv2.imdecode(np.frombuffer(
                        self.feed.get_image_jpg(r.date, r.event, r.timestamp), 
                        dtype='uint8'), -1)
                    ((x1, y1, x2, y2), facemarks) = self.facelist.format_facemarks(r)
                    if y1 < 0: y1 = 0 
                    if x1 < 0: x1 = 0 
                    face = image[y1:y2, x1:x2]
                    if len(face) > 0:
                        if face.shape[1] < 96: face = imutils.resize(face, width=96, inter=cv2.INTER_CUBIC)
                        if r.dx != 0:
                            aligned = self.fa.align(face, facemarks)
                        else:
                            aligned = face
                        embeddings = self.fe.detect(aligned, (0,0,aligned.shape[1],aligned.shape[0]))
                        refkey = self.facelist.format_refkey(r)
                        if refkey in hdf5.keys(): del hdf5[refkey]
                        hdf5[refkey] = embeddings
                        self.facelist.set_status(r.Index, 2, self.taskDate)
                        update_cnt += 1
            # Should always push any updates back to data sink. SFTP? 
            if update_cnt:
                self.facelist.commit()
                self.publish(f'FaceDataUpdate: {update_cnt} face selections processed for {self.taskDate}.')
        return False

class DailyCleanup(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.dataFeed = feed
        self.event_date = jobreq.eventDate
        self.performing_deletes = cfg["run_deletes"]
        self.face_ratio_cutoff = cfg['face_ratio']
        self.face_confidence = cfg['confidence']

    def pipeline(self, frame) -> bool:
        # This is a one-shot pipeline to process all events within the eventDate 
        cwIndx = self.dataFeed.get_date_index(self.event_date)
        types = cwIndx['type'].value_counts()
        trk_cnt = types.get('trk', 0)  # event count
        faces_cnt = types.get('fd1', 0)
        if trk_cnt:
            face_ratio = faces_cnt / trk_cnt
            if face_ratio > self.face_ratio_cutoff: 
                trk_evts = cwIndx.loc[cwIndx['type'] == 'trk']['event'].to_list()
                face_evts = cwIndx.loc[cwIndx['type'] == 'fd1']['event'].to_list()
                recon_evts = cwIndx.loc[cwIndx['type'] == 'fr1']['event'].to_list()
                # Purge any events with no detected faces.
                delete_evts = [e for e in trk_evts if not e in face_evts]
                # Also include any face events with no recon result.
                delete_evts.extend([e for e in face_evts if not e in recon_evts])
                for event in recon_evts:
                    recon = self.dataFeed.get_tracking_data(self.event_date, event, 'fr1')
                    recon['proba'] = recon.apply(lambda x: float(str(x['classname']).split()[1][:-1])/100, axis=1)
                    recon['usable'] = recon.apply(lambda x: str(x['classname'])[-1:], axis=1)
                    bestfaces = recon.loc[recon['usable'] == '*']
                    if len(bestfaces.index) > 0:
                        continue
                    highscores = recon.loc[recon['proba'] > self.face_confidence]
                    if len(highscores.index) > 0:
                        continue
                    # Purge any event where face recon found only poor quality face images and low confidence recognitions.
                    delete_evts.append(event)
                if self.performing_deletes:
                    for event in delete_evts: self.dataFeed.delete_event(self.event_date, event)
                else:
                    for event in delete_evts:
                        trkrs = cwIndx.loc[cwIndx['event'] == event]['type'].to_list()
                        started = cwIndx.loc[cwIndx['event'] == event]['timestamp'].min()
                        imgs = self.dataFeed.get_image_list(self.event_date, event)
                        setlen = {trk: len(self.dataFeed.get_tracking_data(self.event_date, event, trk).index) for trk in trkrs}
                        self.publish(f"DailyCleanup, [{event}] {started}, imgs: {len(imgs):3} trkrs: {setlen}")
                stats = f"DailyCleanup {self.event_date}, trk: {trk_cnt}, fd1: {faces_cnt}, fr1: {len(recon_evts)}, to delete: {len(delete_evts)} ({self.performing_deletes})"
            else:
                stats = f"DailyCleanup, face detection ratio of {round(face_ratio,2)} below cutoff threshold, (trk: {trk_cnt}, fd1: {faces_cnt}), no action taken"
            self.publish(stats)
        return False

class DiskMonitor(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:    
        self.startDate = jobreq.eventDate
        self.feed = feed

    def pipeline(self, frame) -> bool:
        # Get the complete list of dates available through the DataFeed
        event_dates = self.feed.get_date_list()
        target_dates = [d for d in event_dates if d >= self.startDate]  
        for evtDate in target_dates:  
            dateTag = ('DiskMonitor', evtDate)
            try:
                # Get the camwatcher event index for a given date
                cwIndx = self.feed.get_date_index(evtDate)
                trkrs = cwIndx.loc[cwIndx['type'] == 'trk']
                if len(trkrs.index) > 0:
                    for evt in trkrs[:].itertuples():
                        event = evt.event
                        node = evt.node
                        view = evt.viewname
                        imgs = self.feed.get_image_list(evtDate, event)
                        evt_time, objcnt, persons, tails, tail_time = 0, 0, 0, 0, 0
                        if len(imgs) > 0:
                            evtData = cwIndx.loc[cwIndx['event'] == evt]
                            evtelaps = imgs[-1]-imgs[0]
                            evt_time = round(evtelaps.seconds + evtelaps.microseconds/100000,2)
                            trkTypes = [t for t in evtData['type']]
                            # TODO: Need a full analysis of event data by date. For now,
                            # just focusing on "person" detections. Looking to trim the 
                            # tail end of the image captures beyond the last detection.
                            objs = self.feed.get_tracking_data(evtDate, event, 'obj')
                            if len(objs.index):
                                objcnt = len(objs.index)
                                objs['name'] = objs.apply(lambda x: str(x['classname']).split(':')[0], axis=1)
                                persons = objs.loc[objs['name'] == 'person']
                                if len(persons.index) > 0:
                                    personcnt = len(persons.index)
                                    lastTrk = persons.iloc[-1].timestamp
                                    tailend = [t for t in imgs if t > lastTrk]
                                    tails = len(tailend)
                                    if tails > 0:
                                        tailelaps = tailend[-1]-tailend[0]
                                        tail_time = round(tailelaps.seconds + tailelaps.microseconds/100000,2)
                                        result = dateTag + (event, node, view, len(imgs), evt_time, len(trkTypes), 
                                                            objcnt, personcnt, tails, round(tails/len(imgs)*100,2), tail_time) 
                                        self.publish(result)
                                        # TODO: Just logging these, so not practical. If trimming the tail of the 
                                        # image captures from events is to be a thing, need a better solution.
                                        for img in [t.isoformat() for t in tailend]:
                                            fpath = "{}/{}_{}_{}.jpg".format(
                                                evtDate,
                                                event,
                                                img[:10], 
                                                img[11:].replace(':','.')
                                            )
                                            self.publish(('DiskMonitor', 'framefile', fpath))
                else:
                    result = dateTag + ("ERROR", "camwatcher index is empty")
                    self.publish(result)
            except DataFeed.ImageSetEmpty as e:
                result = dateTag + ("ERROR", f"No image data for {e.date},{e.evt}")
            except DataFeed.TrackingSetEmpty as e:
                result = dateTag + ("ERROR", f"No tracking data for {e.date},{e.evt},{e.trk}")
            except Exception as e:
                result = dateTag + ("ERROR", f"exception retrieving event data: {str(e)}")
                self.publish(result)
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
                trkrs = cwIndx.loc[cwIndx['type'] == 'trk']
                if len(trkrs.index) > 0:
                    # Process every event for this date
                    for _evt in trkrs[:].itertuples():
                        event = _evt.event
                        node = _evt.node
                        view = _evt.viewname
                        imgs = self.feed.get_image_list(evtDate, event)
                        if len(imgs) > 0:
                            try:
                                jpeg = self.feed.get_image_jpg(evtDate, event, imgs[0])
                                if jpeg is not None:
                                    #frame = simplejpeg.decode_jpeg(jpeg, colorspace='BGR')
                                    frame = cv2.imdecode(np.frombuffer(jpeg, dtype='uint8'), -1)
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
        self.event_date = jobreq.eventDate
        self.dataFeed = feed
        self.od = MobileNetSSD(cfg["mobilenetssd"], accelerator)

    def pipeline(self, frame) -> bool:
        cwIndx = self.dataFeed.get_date_index(self.event_date)
        trkEvts = cwIndx.loc[cwIndx['type'] == 'trk']
        for cwEvt in trkEvts[:].itertuples():
            # For every event in the date...
            start_time = time.time()
            event = cwEvt.event
            eventKey = (self.event_date, event)
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
        'GetFaces'               : GetFaces,
        'FaceRecon'              : FaceRecon,
        'FaceSweep'              : FaceSweep,
        'FaceDataUpdate'         : FaceDataUpdate,
        'MobileNetSSD_allFrames' : MobileNetSSD_allFrames,
        'DailyCleanup'           : DailyCleanup,
        'DiskMonitor'            : DiskMonitor,
        'CollectImageSizes'      : CollectImageSizes,
        'MeasureRingLatency'     : MeasureRingLatency
    }
    cfg = readConfig(cfgfile)
    task = menu[jobreq.jobTask](jobreq, trkdata, feed, cfg, accelerator)
    return task 
