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
from scipy.spatial import distance as dist
from sentinelcam.utils import readConfig
from sentinelcam.datafeed import DataFeed
from sentinelcam.facedata import FaceBaselines, FaceList, FaceStats
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
    def finalize(self) -> bool:
        # Optional, for implementing end of task finalization logic.
        # Return False to cancel any chained task.
        return True

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
        self.fd = FaceDetector(cfg['face_detection'], accelerator)
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

    def _publish_face_rects(self, faces, labels) -> None:
        if len(faces) > 0:
            for i, face in enumerate(faces):
                result = ("Face", i, int(face[0]), int(face[1]), int(face[2]), int(face[3]))
                if not self.cwUpd:
                    result = result + tuple((x for x in labels[i].split(' ', 1)[1:]))  # append detailed label info if not updating camwatcher
                self.publish(result, self.refkey, self.cwUpd)
                self.face_cnt += 1

    def pipeline(self, frame) -> bool:
        if self.allFrames:
            (faces, labels) = self.fd.detect(frame)
            if len(faces) > 0: self._publish_face_rects(faces, labels)
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
                            (faces, labels) = self.fd.detect(image)  # finds every face in the image
                            if len(faces) > 0: self._publish_face_rects(faces, labels)
                            search_not_completed = False
                            self._search_cnt += 1
                        self.trkRec = next(self.cursor)
                    # Internal tracking for timestamp of current frame.
                    self.frametime = next(self.frames)
                except StopIteration:
                    return False      # Reached last detected person, end the task.
        return True

    def finalize(self) -> bool:
        results = ('Faces', self.event_date, self.event_id, len(self.imgs), len(self.persons.index), self._search_cnt, self.face_cnt)
        self.publish(results)
        return self.face_cnt > 0

class FaceRecon(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator) -> None:
        self.cwUpd = cfg['camwatcher_update']
        self.refkey = cfg['trk_type']
        self.trkdata = trkdata
        #self.trkcnt = len(trkdata.index)
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
        facecnt = len(self.trkdata.loc[self.trkdata['timestamp'] == self.frametime].index)
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
            distance, margin = 0,0
            if proba > 0.97:
                (distance, margin) = self.fb.compare(embeddings, j)
                if distance > 0.99:
                    # seek confirmation, have high confidence with a large distance
                    (k, distance) = self.fb.search(embeddings)
                    margin = distance - self.fb.thresholds()[k]
                    if k != j:
                        proba = 0
                        if distance > 0.99:
                            if candidate:
                                name, j = 'Unknown', self.unk
                        else:
                            name, j = self.labels.classes_[k], k
            else:
                (k, distance) = self.fb.search(embeddings)
                margin = distance - self.fb.thresholds()[k]
                if k != j:
                    proba = 0
                    if distance > 0.99:
                        if candidate:
                            name, j = 'Unknown', self.unk
                    else:
                        name, j = self.labels.classes_[k], k
            if margin < 0.05:
                # TODO: Parameterize (or improve) this. Always consider these as possible candidates
                # for inclusion in recognition model, since distance within fudge factor over threshold?
                candidate = True
            flag = 1 if candidate else 0
            stats = FaceStats(distance, margin, flag, facecnt)
            if candidate or name != 'Unknown':
                classlabel = "{}: {:.2f}% {}".format(name, proba * 100, stats.format())
                result = (classlabel, self.trkRec.objid, x1, y1, x2, y2)
                self.publish(result, self.refkey, self.cwUpd)
                self.cnts[j] += 1
            try:
                self.trkRec = next(self.trkrecs)
            except StopIteration:
                return False
        self.frametime = self.trkRec.timestamp
        return True

    def finalize(self) -> True:
        namelist = [self.labels.classes_[n] for n in range(len(self.labels.classes_))]
        namelist.append('Unknown')
        cnts = ", ".join([f"{namelist[n]} {self.cnts[n]}" for n in range(len(self.cnts)) if self.cnts[n]>0])
        results = ('Recon', self.event_date, self.event_id, self.facecnt, cnts)
        self.publish(results)
        return True

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
        facestats = FaceStats(None,None,None,None)
        refkeys = [self.facelist.format_refkey(r) for r in self.facelist.get_fullset()[:].itertuples()]
        new_faces = 0
        prev_hash = 0
        cwIndx = self.feed.get_date_index(self.taskDate)
        # TODO: protect existing selections from being inadvertently over-written with new/duplicated data.
        # Probably OK to permit this if not included in the subset with non-zero status flags. But should first
        # remove any exsiting content already held for that event.
        for sweepchk in cwIndx.loc[cwIndx['type'] == self.ref_type].itertuples():
            try:
                trkdata = facestats.df_apply(self.feed.get_tracking_data(self.taskDate, sweepchk.event, self.ref_type))
                usable = trkdata.loc[trkdata['usable'] == 1]
                targets = usable.loc[
                    (usable['proba'] > 0.99) |
                    ((usable['proba'] == 0 ) & (usable['name'] == 'Unknown') & (usable['distance'] > 0.99)) |
                    ((usable['proba'] == 0 ) & (usable['name'] != 'Unknown') & (usable['margin'] < 0.05))
                ]
                if len(targets.index) > 0:
                    for consider in targets[:].itertuples():
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
                                    'dist': round(consider.distance, 6),
                                    'margin': round(consider.margin, 6),
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
            except DataFeed.TrackingSetEmpty:
                pass
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
        self.run_date = jobreq.eventDate  # Date when cleanup task is running
        self.performing_deletes = cfg["run_deletes"]
        self.max_scan_days = cfg.get('max_scan_days', 30)
        self.retention_profiles = cfg.get('retention_profiles', {})

        # Build node-to-profile mapping for fast lookup
        self.node_profiles = {}
        for profile_name, profile_config in self.retention_profiles.items():
            for node in profile_config.get('nodes', []):
                self.node_profiles[node] = (profile_name, profile_config)

        # Get default profile for nodes not explicitly assigned
        self.default_profile = self.retention_profiles.get('default', {
            'strategy': 'minimal_retention',
            'retention_days': 7
        })

    def pipeline(self, frame) -> bool:
        """Scan backwards through dates and apply retention policies per node"""
        from datetime import datetime
        from collections import defaultdict

        total_deleted = 0
        total_scanned = 0
        dates_processed = 0

        # Use input date as the reference point (run_date is in YYYY-MM-DD format)
        run_date = datetime.fromisoformat(self.run_date)

        # Get list of dates that actually have data
        available_dates = self.dataFeed.get_date_list()

        self.publish(f"DailyCleanup starting from {self.run_date}: found {len(available_dates)} dates with data")

        # Process each date that is on or before the run_date
        for scan_date_str in available_dates:
            scan_date = datetime.fromisoformat(scan_date_str)

            # Only process dates on or before the run_date
            if scan_date > run_date:
                continue

            # Calculate how many days old this date's events are
            event_age_days = (run_date - scan_date).days

            # Only process dates within our scan window
            if event_age_days > self.max_scan_days:
                continue

            try:
                cwIndx = self.dataFeed.get_date_index(scan_date_str)
                if len(cwIndx) == 0:
                    continue  # No data for this date

                total_scanned += len(cwIndx)
                dates_processed += 1

                # Track stats per strategy for this date
                date_stats = defaultdict(lambda: {'total': 0, 'deleted': 0})

                # Group events by node for profile-specific processing
                for node in cwIndx['node'].unique():
                    node_events = cwIndx[cwIndx['node'] == node]
                    profile_name, deleted_count, event_count = self._process_node_events(
                        node, node_events, scan_date_str, event_age_days
                    )
                    date_stats[profile_name]['total'] += event_count
                    date_stats[profile_name]['deleted'] += deleted_count
                    total_deleted += deleted_count

                # Generate daily summary if any deletions occurred
                date_total_deleted = sum(s['deleted'] for s in date_stats.values())
                if date_total_deleted > 0:
                    summary_parts = [
                        f"{strategy}[{stats['total']},{stats['deleted']}]"
                        for strategy, stats in sorted(date_stats.items())
                        if stats['deleted'] > 0
                    ]
                    self.publish(f"DailyCleanup {scan_date_str} age:{event_age_days}d: {' '.join(summary_parts)}")

            except Exception as e:
                self.publish(f"DailyCleanup error processing {scan_date_str}: {str(e)}")
                continue

        stats = f"DailyCleanup completed: processed {dates_processed} dates, scanned {total_scanned} events, deleted {total_deleted} ({self.performing_deletes})"
        self.publish(stats)
        return False

    def _process_node_events(self, node, node_events, date_str, event_age_days):
        """Apply retention policy for a specific node's events on a given date

        Evaluates EACH event based on ALL data types it contains.
        Keeps event if ANY data type has value (quality faces, speed data, etc.)

        Returns:
            tuple: (profile_name, deleted_count, total_event_count)
        """
        # Get retention profile for this node
        profile_name, profile = self.node_profiles.get(node, ('default', self.default_profile))
        strategy = profile.get('strategy', 'minimal_retention')

        # Get unique events for this node
        all_events = node_events.loc[node_events['type'] == 'trk']['event'].unique()
        total_events = len(all_events)

        if strategy == 'never_delete':
            return (profile_name, 0, total_events)

        # Build event-to-datatypes mapping for efficient lookup
        events_by_type = {}
        for evt_type in node_events['type'].unique():
            events_with_type = set(node_events.loc[node_events['type'] == evt_type]['event'].unique())
            events_by_type[evt_type] = events_with_type

        # Evaluate each event individually based on ALL its data types
        delete_evts = []
        for event in all_events:
            # What data types does this event have?
            event_data_types = [dt for dt, events in events_by_type.items() if event in events]

            # Should we delete this event? (only if ALL data is non-valuable)
            should_delete = self._evaluate_event_retention(
                event, event_data_types, node_events, date_str,
                event_age_days, profile, strategy
            )

            if should_delete:
                delete_evts.append(event)

        # Execute deletions
        if delete_evts and self.performing_deletes:
            for event in delete_evts:
                self.dataFeed.delete_event(date_str, event)

        return (profile_name, len(delete_evts), total_events)

    def _extract_speed(self, classname):
        try:
            # Split and try to parse the first part as float
            return float(str(classname).split()[0])
        except (ValueError, IndexError):
            return np.nan

    def _evaluate_event_retention(self, event, data_types, node_events,
                                  date_str, event_age_days, profile, strategy):
        """Evaluate whether a single event should be deleted

        Returns True only if ALL data types in the event are past retention or low-value.
        If ANY data type has value, returns False (keep the event).
        """
        retention_days = profile.get('retention_days', 7)

        # Not old enough for base retention yet
        if event_age_days <= retention_days:
            return False

        has_valuable_data = False

        # Check vehicle speed data (valuable high speeds within extended retention)
        if 'vsp' in data_types:
            speed_cutoff = profile.get('speed_cutoff', 30.0)  # mph
            extended_days = profile.get('extended_days', 30)
            try:
                speed_data = self.dataFeed.get_tracking_data(date_str, event, 'vsp')
                if len(speed_data.index) > 0:
                    speed_data['mph'] = speed_data['classname'].apply(self._extract_speed)
                    has_valuable_data = any(speed_data['mph'] > speed_cutoff)
                    if event_age_days <= extended_days:
                        has_valuable_data = True
            except DataFeed.TrackingSetEmpty:
                pass

        # Check face recognition quality
        if 'fr1' in data_types and not has_valuable_data:
            try:
                facestats = FaceStats(None, None, None, None)
                recon = facestats.df_apply(
                    self.dataFeed.get_tracking_data(date_str, event, 'fr1')
                )
                confidence_threshold = profile.get('confidence_threshold', 0.975)

                # Keep if has usable faces OR high confidence recognition
                if len(recon.loc[recon['usable'] == 1].index) > 0:
                    has_valuable_data = True
                elif len(recon.loc[recon['proba'] > confidence_threshold].index) > 0:
                    has_valuable_data = True
            except DataFeed.TrackingSetEmpty:
                pass

        # Check for face detection without recognition (fd1 but no fr1)
        # These are less valuable, only count if within retention period
        if 'fd1' in data_types and 'fr1' not in data_types and not has_valuable_data:
            # Has faces but no recognition attempted - borderline valuable
            # Apply face detection ratio safety check
            if strategy == 'face_quality':
                types = node_events['type'].value_counts()
                trk_cnt = types.get('trk', 0)
                faces_cnt = types.get('fd1', 0)
                if trk_cnt > 0:
                    face_ratio = faces_cnt / trk_cnt
                    min_face_ratio = profile.get('min_face_ratio', 0.15)
                    if face_ratio > min_face_ratio:
                        # Face detection appears operational, but no recognition
                        # Only valuable if very recent
                        if event_age_days <= (retention_days * 0.5):
                            has_valuable_data = True

        # Future: Add checks for other data types here
        # if 'pet' in data_types:
        #     has_valuable_data = True  # Always keep pet detections
        # if 'lpr' in data_types:  # license plate recognition
        #     has_valuable_data = True

        # Delete only if NO valuable data found
        return not has_valuable_data

class VehicleTracker:
    """Tracks a single vehicle across frames"""
    def __init__(self, track_id, centroid, bbox):
        self.track_id = track_id
        self.centroid = centroid
        self.bbox = bbox
        self.marker_crossings = []  # List of {marker, direction, timestamp}
        self.current_speed = None  # Current speed estimate
        self.direction = None  # Overall direction (LR or RL)

    def update(self, centroid, bbox):
        self.centroid = centroid
        self.bbox = bbox

    def add_marker_crossing(self, marker_index, direction, timestamp):
        # Avoid duplicate crossings
        if len(self.marker_crossings) > 0:
            last = self.marker_crossings[-1]
            if last['marker'] == marker_index and last['direction'] == direction:
                return

        self.marker_crossings.append({
            'marker': marker_index,
            'direction': direction,
            'timestamp': timestamp
        })

        # Set direction from first crossing
        if self.direction is None:
            self.direction = direction

    def recalculate_speed(self, marker_street_positions, min_markers, calibration_factor=1.0):
        """Recalculate speed after each marker crossing using perspective-corrected positions"""
        if len(self.marker_crossings) < min_markers:
            return None

        # Get first and last crossing
        first = self.marker_crossings[0]
        last = self.marker_crossings[-1]

        # Ensure consistent direction
        if first['direction'] != last['direction']:
            return None

        # Calculate distance using perspective-corrected marker positions
        distance_meters = abs(
            marker_street_positions[last['marker']] -
            marker_street_positions[first['marker']]
        )

        # Calculate time (seconds)
        time_delta = (last['timestamp'] - first['timestamp']).total_seconds()
        if time_delta <= 0:
            return None

        # Calculate speed (mph) and apply calibration factor
        speed_mps = distance_meters / time_delta
        speed_mph = speed_mps * 2.23694 * calibration_factor  # meters/sec to mph with calibration

        self.current_speed = round(speed_mph, 1)
        return self.current_speed

class VehicleSpeed(Task):
    def __init__(self, jobreq, trkdata, feed, cfg, accelerator, image_timestamps=None) -> None:
        self.cwUpd = cfg['camwatcher_update']
        self.refkey = cfg['refkey']
        self.event_id = jobreq.eventID
        self.event_date = jobreq.eventDate

        # Create timestamp-to-offset mapping for batch publishing
        self.timestamp_to_offset = {}
        if image_timestamps is not None:
            for offset, timestamp in enumerate(image_timestamps):
                self.timestamp_to_offset[timestamp] = offset

        # Filter tracking data for vehicles only
        # Note: classname includes confidence like "car: 0.96", so check prefix
        vehicle_classes = cfg['vehicle_classes']
        mask = trkdata['classname'].str.startswith(tuple(f"{v}:" for v in vehicle_classes))
        self.vehicles = trkdata.loc[mask]

        # Marker configuration
        self.markers_x = cfg['markers_x']
        self.marker_y = cfg['marker_y']
        self.marker_y_tolerance = cfg['marker_y_tolerance']

        # Distance and speed configuration
        self.street_length = cfg['street_length_meters']
        self.camera_distance = cfg['camera_distance_meters']
        self.viewport_width = cfg['viewport_width_meters']
        self.speed_limit = cfg['speed_limit_mph']
        self.speed_tolerance = cfg['speed_tolerance_mph']
        self.min_markers = cfg['min_markers']
        self.speed_calibration_factor = cfg.get('speed_calibration_factor', 1.0)

        # Tracking parameters
        self.centroid_max_distance = cfg['centroid_max_distance']

        # Active vehicle trackers: dict[track_id] = VehicleTracker
        self.trackers = {}
        self.next_track_id = 1
        self.violation_count = 0
        self.tracking_records_published = 0
        self.vehicles_with_speed = 0

        # Frame dimensions (will be set from first frame in pipeline)
        self.frame_width = None
        self.frame_height = None
        self.marker_pixels = []
        self.marker_street_positions = []  # Perspective-corrected street positions
        self.marker_y_min = None
        self.marker_y_max = None

    def _initialize_markers(self, frame_shape):
        """Convert marker percentages to pixel coordinates and calculate perspective-corrected positions"""
        self.frame_height, self.frame_width = frame_shape[:2]
        self.marker_pixels = [int(x * self.frame_width) for x in self.markers_x]

        # Calculate perspective-corrected street positions for each marker
        self.marker_street_positions = self._pixel_to_street_position(
            np.array(self.marker_pixels)
        )

        # Y-range for vehicle detection at marker lines
        marker_y_center = int(self.marker_y * self.frame_height)
        tolerance_pixels = int(self.marker_y_tolerance * self.frame_height)
        self.marker_y_min = marker_y_center - tolerance_pixels
        self.marker_y_max = marker_y_center + tolerance_pixels

    def _pixel_to_street_position(self, pixel_x):
        """Convert pixel X coordinate to actual street position in meters using perspective correction

        Args:
            pixel_x: X coordinate(s) in pixels (can be array or scalar)

        Returns:
            Street position(s) in meters from camera centerline
        """
        # Normalize to -0.5 to +0.5 (center = 0)
        normalized_x = (pixel_x / self.frame_width) - 0.5

        # Calculate angle from camera centerline
        angle = np.arctan(normalized_x * self.viewport_width / self.camera_distance)

        # Calculate physical position along street
        street_position = self.camera_distance * np.tan(angle)

        return street_position

    def _get_centroid(self, x1, y1, x2, y2):
        """Calculate centroid of bounding box"""
        cx = int((x1 + x2) / 2)
        cy = int((y1 + y2) / 2)
        return (cx, cy)

    def _in_marker_zone(self, cy):
        """Check if centroid Y is within acceptable marker zone"""
        return self.marker_y_min <= cy <= self.marker_y_max

    def _check_marker_crossing(self, prev_cx, curr_cx, marker_x):
        """Detect if vehicle crossed marker line between frames"""
        # Left-to-right crossing
        if prev_cx < marker_x <= curr_cx:
            return 'LR'
        # Right-to-left crossing
        elif prev_cx > marker_x >= curr_cx:
            return 'RL'
        return None

    def _publish_tracking_record(self, tracker, timestamp):
        """Publish per-frame tracking records"""
        x1, y1, x2, y2, classname = tracker.bbox

        # Format direction for display
        direction_str = None
        if tracker.direction == 'LR':
            direction_str = 'L-to-R'
        elif tracker.direction == 'RL':
            direction_str = 'R-to-L'

        # Publish to vsp tracking type with classname field containing speed info
        if tracker.current_speed is not None:
            speed_label = f"{tracker.current_speed} mph: {direction_str}" if direction_str else f"{tracker.current_speed} mph"
        else:
            speed_label = f"{direction_str}" if direction_str else ""

        if speed_label != "":
            # Build a standard tracking type data record
            result = (
                speed_label,               # Vehicle speed and direction
                tracker.track_id,          # Track ID
                int(x1), int(y1),          # Bounding box
                int(x2), int(y2)
            )

            # Look up frame offset from timestamp for proper camwatcher correlation
            offset = self.timestamp_to_offset.get(timestamp, 0)
            self.publish(result, self.refkey, self.cwUpd, offset_override=offset)
            self.tracking_records_published += 1

    def _update_trackers(self, detections, timestamp):
        """Update vehicle trackers with new detections"""
        # Extract centroids from current detections
        current_centroids = []
        current_boxes = []
        for det in detections:
            cx, cy = self._get_centroid(det.rect_x1, det.rect_y1, det.rect_x2, det.rect_y2)
            if self._in_marker_zone(cy):
                current_centroids.append((cx, cy))
                current_boxes.append((det.rect_x1, det.rect_y1, det.rect_x2, det.rect_y2, det.classname))

        # If no detections in marker zone this timestamp, skip
        if len(current_centroids) == 0:
            return

        # Match detections to existing trackers
        if len(self.trackers) == 0:
            # No existing trackers, register all as new
            for i, (cx, cy) in enumerate(current_centroids):
                tracker = VehicleTracker(self.next_track_id, (cx, cy), current_boxes[i])
                self.trackers[self.next_track_id] = tracker
                self.next_track_id += 1
                # Publish tracking record for this frame
                self._publish_tracking_record(tracker, timestamp)
        else:
            # Match current detections to existing trackers
            tracker_ids = list(self.trackers.keys())
            tracker_centroids = [self.trackers[tid].centroid for tid in tracker_ids]

            # Compute distance matrix
            D = dist.cdist(np.array(tracker_centroids), np.array(current_centroids))

            # Find minimum distance matches
            rows = D.min(axis=1).argsort()
            cols = D.argmin(axis=1)[rows]

            used_rows = set()
            used_cols = set()

            for (row, col) in zip(rows, cols):
                if row in used_rows or col in used_cols:
                    continue
                if D[row, col] > self.centroid_max_distance:
                    continue

                # Update existing tracker
                track_id = tracker_ids[row]
                prev_cx = self.trackers[track_id].centroid[0]
                curr_cx = current_centroids[col][0]

                self.trackers[track_id].update(current_centroids[col], current_boxes[col])

                # Check for marker crossings
                for i, marker_x in enumerate(self.marker_pixels):
                    direction = self._check_marker_crossing(prev_cx, curr_cx, marker_x)
                    if direction:
                        self.trackers[track_id].add_marker_crossing(i, direction, timestamp)
                        # Recalculate speed after crossing marker
                        self.trackers[track_id].recalculate_speed(
                            self.marker_street_positions, self.min_markers, self.speed_calibration_factor
                        )

                # Publish tracking record for this frame
                self._publish_tracking_record(self.trackers[track_id], timestamp)

                used_rows.add(row)
                used_cols.add(col)

            # Register new trackers for unused detections (new vehicles entering scene)
            unused_cols = set(range(len(current_centroids))) - used_cols
            for col in unused_cols:
                tracker = VehicleTracker(self.next_track_id, current_centroids[col], current_boxes[col])
                self.trackers[self.next_track_id] = tracker
                self.next_track_id += 1
                # Publish tracking record for this frame
                self._publish_tracking_record(tracker, timestamp)

    def pipeline(self, frame) -> bool:
        # One-shot pipeline: process all tracking data in single call
        if len(self.vehicles.index) == 0:
            return False

        # Initialize markers from first frame
        self._initialize_markers(frame.shape)

        # Process all vehicle detections chronologically
        frames_with_vehicles = self.vehicles.groupby('timestamp')
        for timestamp in sorted(frames_with_vehicles.groups.keys()):
            frame_vehicles = frames_with_vehicles.get_group(timestamp)
            detections = list(frame_vehicles.itertuples())
            self._update_trackers(detections, timestamp)

        # Process any remaining trackers at end
        for track_id in list(self.trackers.keys()):
            tracker = self.trackers[track_id]
            if tracker.current_speed is not None:
                self.vehicles_with_speed += 1
            if tracker.current_speed and tracker.current_speed > (self.speed_limit + self.speed_tolerance):
                self.violation_count += 1
                direction = 'L-to-R' if tracker.direction == 'LR' else 'R-to-L'
                self.publish(
                    f"SPEED VIOLATION: {tracker.current_speed} mph {direction} "
                    f"(limit {self.speed_limit} mph) - "
                    f"Event {self.event_date}/{self.event_id}"
                )

        return False  # Done in one shot

    def finalize(self) -> bool:
        # Summary: VehicleSpeed, date, event, vehicle_detections, unique_vehicles, vehicles_with_speed, vsp_records, violations
        results = (
            'VehicleSpeed', self.event_date, self.event_id,
            len(self.vehicles.index), self.next_track_id - 1,
            self.vehicles_with_speed, self.tracking_records_published,
            self.violation_count
        )
        self.publish(results)

        return self.tracking_records_published > 0

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

def TaskFactory(jobreq, trkdata, feed, cfgfile, accelerator, image_timestamps=None) -> Task:
    menu = {
        'GetFaces'               : GetFaces,
        'FaceRecon'              : FaceRecon,
        'FaceSweep'              : FaceSweep,
        'FaceDataUpdate'         : FaceDataUpdate,
        'MobileNetSSD_allFrames' : MobileNetSSD_allFrames,
        'VehicleSpeed'           : VehicleSpeed,
        'DailyCleanup'           : DailyCleanup,
        'MeasureRingLatency'     : MeasureRingLatency
    }
    cfg = readConfig(cfgfile)
    # Pass image_timestamps to tasks that need timestamp-to-offset mapping
    if jobreq.jobTask == 'VehicleSpeed' and image_timestamps is not None:
        task = menu[jobreq.jobTask](jobreq, trkdata, feed, cfg, accelerator, image_timestamps)
    else:
        task = menu[jobreq.jobTask](jobreq, trkdata, feed, cfg, accelerator)
    return task
