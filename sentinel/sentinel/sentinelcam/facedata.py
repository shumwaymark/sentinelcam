"""facedata: Access to facial data used for recognition modeling

Copyright (c) 2023 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import h5py
import numpy as np
import pandas as pd
from ast import literal_eval
from datetime import datetime

def findEuclideanDistance(source_representation, test_representation):
    euclidean_distance = source_representation - test_representation
    euclidean_distance = np.sum(np.multiply(euclidean_distance, euclidean_distance))
    euclidean_distance = np.sqrt(euclidean_distance)
    return euclidean_distance

class FaceStats:
    def __init__(self, distance, margin, candidate, facecnt) -> None:
        self.distance = distance
        self.margin = margin
        self.candidate = candidate
        self.facecnt = facecnt

    def format(self) -> str:
        return f"({self.distance:.6f}|{self.margin:.6f}|{self.candidate}|{self.facecnt})"
    
    def parse(self, istr) -> tuple:
        flds = literal_eval(istr.replace('|',','))
        if len(flds) == 4:
            (self.distance, self.margin, self.candidate, self.facecnt) = flds
        return flds

    def df_apply(self, df) -> pd.DataFrame:
        df['name']     = df.apply(lambda x: str(x['classname']).split(':')[0], axis=1)
        df['proba']    = df.apply(lambda x: float(str(x['classname']).split()[1][:-1])/100, axis=1)
        df['distance'] = df.apply(lambda x: self.parse(str(x['classname']).split()[2])[0], axis=1)
        df['margin']   = df.apply(lambda x: self.parse(str(x['classname']).split()[2])[1], axis=1)
        df['usable']   = df.apply(lambda x: self.parse(str(x['classname']).split()[2])[2], axis=1)
        df['facecnt']  = df.apply(lambda x: self.parse(str(x['classname']).split()[2])[3], axis=1)
        df['cx']       = df.apply(lambda x: int(x['rect_x1']+(x['rect_x2']-x['rect_x1'])//2), axis=1)
        df['cy']       = df.apply(lambda x: int(x['rect_y1']+(x['rect_y2']-x['rect_y1'])//2), axis=1)
        return df
    
class FaceBaselines:
    def __init__(self, baselines, names) -> None:
        self._baselines = h5py.File(baselines,'r')
        self._namelist = names
        self._faces = [self._baselines[face] for face in names]  # map by face index
        self._thresholds = [self._baselines[face].attrs.get('threshold') for face in names]

    def thresholds(self) -> list:
        return(self._thresholds)
    
    def compare(self, face, who) -> tuple:
        distance = findEuclideanDistance(face, self._faces[who])
        margin = distance - self._thresholds[who]
        return (distance, margin)

    def search(self, face) -> tuple:
        distances = np.array([findEuclideanDistance(face, self._faces[i]) for i in range(len(self._faces))])
        min_dist = np.argmin(distances)
        return (min_dist, distances[min_dist])

class FaceList:
    def __init__(self, csvfile='facelist.csv', sink='data1') -> None:
        self._mysink = sink
        self._csvfile = csvfile
        self.load_data()

    def load_data(self, csvfile=None) -> None:
        if csvfile is not None:
            self._csvfile = csvfile
        self.faces = pd.read_csv(self._csvfile, parse_dates=['timestamp']) 
        self._clean = True

    def event_locked(self, date, event) -> bool:
        subset = self.faces.loc[(self.faces['date'] == date) & (self.faces['event'] == event)]
        return True if len(subset.index) != 0 else False

    def get_idx(self, date, event, timestamp, objid) -> int: 
        facerec = self.faces.loc[(self.faces['date'] == date) & 
                                 (self.faces['event'] == event) &
                                 (self.faces['timestamp'] == timestamp) &
                                 (self.faces['objid'] == objid)]
        if len(facerec.index) != 0:
            idx = facerec.iloc[0].name
        else:
            idx = -1
        return idx

    def format_refkey(self, r) -> str:
        # Helper function to get a formatted "facedata" refkey from a record
        frametime = r.timestamp.isoformat()
        rect = ','.join([str(p) for p in (r.x1, r.y1, r.x2, r.y2)])
        facerect = f"({rect})"
        return f"/{r.name}/{'|'.join([r.date, r.event, frametime, facerect, self._mysink])}"
    
    def format_facemarks(self, r) -> tuple:
        # Helper function to pull face stats from a record
        x1 = int(r.x1)
        y1 = int(r.y1)
        x2 = int(r.x2)
        y2 = int(r.y2)
        rightEye = (int(r.rx), int(r.ry))
        leftEye = (int(r.lx), int(r.ly))
        distance = (int(r.dx), int(r.dy))
        angle = float(r.angle)
        focus = float(r.focus)
        facerect = (x1, y1, x2, y2)
        facemarks = (rightEye, leftEye, distance, angle, focus)
        return (facerect, facemarks)
   
    def set_status(self, idx, status, date=datetime.now().isoformat()[:10]) -> None:
        # 0=candidate, 1=selected, 2=in_use, 3=revoked, 4=remove
        self.faces.loc[idx, 'status'] = status
        if status == 2:
            self.faces.loc[idx, 'date_in_use'] = date
        self._clean = False

    def set_name(self, idx, name) -> None:
        self.faces.loc[idx, 'name'] = name
        self._clean = False

    def add_rows(self, df) -> None:
        self.faces = pd.concat([self.faces, df], ignore_index=True)
        self._clean = False

    def get_selections(self) -> pd.DataFrame:
        return self.faces.loc[self.faces['status'] == 1]

    def get_fullset(self) -> pd.DataFrame:
        return self.faces

    def commit(self) -> None:  
        if not self._clean:
            self.faces.to_csv(self._csvfile, index=False)
            self._clean = True

    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self.commit()

    def __del__(self) -> None:
        self.commit()
