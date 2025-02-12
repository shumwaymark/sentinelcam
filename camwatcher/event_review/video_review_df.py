""" video_review:     A trivial Flask application to demonstrate SentinelCam video event review.
                      This uses the CamData class for access to camwatcher data. Original prototype.

    video_review_df:  Revised/enhanced version using the DataFeed class for datapump access. 
                      Allows for operation from within a WSGI environment on an application server.

Copyright (c) 2022 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import time
from datetime import date, datetime, timedelta
from flask import Flask
from flask import Response
from flask import render_template, g
import cv2
import numpy as np
import simplejpeg
from sentinelcam.datafeed import DataFeed

app = Flask(__name__) # initialize a flask object

cfg = {'datapump': 'tcp://data1:5556'} 

@app.before_request
def before_request():
    g.cwFeed = DataFeed(cfg["datapump"])
    g.cwIndx = None
    g.cwEvt = None  # not used?
    g.date = None
    g.event = None
    g.type = None

def _event_selection(date=None, event=None, type=None):
    if date is None:
        g.date = g.cwFeed.get_date_list()[0]
    else:
        g.date = date
    g.cwIndx = g.cwFeed.get_date_index(g.date)        
    if event is None:
        if len(g.cwIndx.index) > 0:
            g.event = g.cwIndx.iloc[0].event
        else:
            g.event = event
    else:
        g.event = event
    g.type = 'trk' if type is None else type
    # appears to be no need for tracking dataframe in the global block?
    g.cwEvt = g.cwFeed.get_tracking_data(g.date, g.event, g.type)

def _generate_event_list(cindx):
    for row in cindx[:].itertuples():
        yield (row.event, row.type, 
            f"{row.timestamp.strftime('%H:%M:%S')} {row.type} {row.node} {row.viewname}")

def _setup_form_data():
    if not g.event: 
        _event_selection()
    subset = g.cwIndx.loc[g.cwIndx["event"] == g.event]
    g.node = subset.iloc[0].node
    g.view = subset.iloc[0].viewname
    g.start = subset["timestamp"].min()
    g.datelist = [(d, date.fromisoformat(d).strftime('%A %B %d, %Y')) for d in g.cwFeed.get_date_list()]
    g.eventlist = [(evt, type, descr) for (evt, type ,descr) in _generate_event_list(g.cwIndx)]

def create_tiny_jpeg() -> bytes:
    pixel = np.zeros((1, 1, 3), dtype=np.uint8)  # 1-pixel image
    buffer = simplejpeg.encode_jpeg(pixel)
    return buffer

class TextHelper:
    def __init__(self, camevt) -> None:
        self._lineType = cv2.LINE_AA
        self._textType = cv2.FONT_HERSHEY_SIMPLEX
        self._textSize = 0.5
        self._thickness = 1
        self._textColors = {}
        self._bboxColors = {}
        for objid in camevt['objid'].unique():
            self._bboxColors[objid] = tuple(int(x) for x in np.random.randint(256, size=3))
            self._textColors[objid] = self.setTextColor(self._bboxColors[objid])
    def setTextColor(self, bgr) -> tuple:
        luminance = ((bgr[0]*.114)+(bgr[1]*.587)+(bgr[2]*.299))/255
        return (0,0,0) if luminance > 0.5 else (255,255,255)
    def putText(self, frame, objid, text, x1, y1, x2, y2):
        (tw, th) = cv2.getTextSize(text, self._textType, self._textSize, self._thickness)[0]
        cv2.rectangle(frame, (x1, y1), (x2, y2), self._bboxColors[objid], 2)
        cv2.rectangle(frame, (x1, (y1 - 28)), ((x1 + tw + 10), y1), self._bboxColors[objid], cv2.FILLED)
        cv2.putText(frame, text, (x1 + 5, y1 - 10), self._textType, self._textSize, self._textColors[objid], self._thickness, self._lineType)

def generate_video(date, event, type='trk'):
    _cwFeed = DataFeed(cfg["datapump"])
    _cwEvt = _cwFeed.get_tracking_data(date, event, type)
    if len(_cwEvt.index) > 0:
        image_list = _cwFeed.get_image_list(date, event)
        if len(image_list) > 0:
            objects = {}                           # object dictionary for holding last known coordinates
            text = TextHelper(_cwEvt)              # select a random color for each distinct object
            event_start = _cwEvt.iloc[0].timestamp
            tracker = _cwEvt[:].itertuples()
            trk = next(tracker)
            trkr_time = trk.timestamp
            playback_begin = datetime.now()
            for frame_time in image_list:
                jpeg = _cwFeed.get_image_jpg(date, event, frame_time)
                frame = simplejpeg.decode_jpeg(jpeg, colorspace='BGR')
                frame_elaps = frame_time - event_start
                if trkr_time < frame_time:
                    try:
                        while trk.timestamp <= frame_time:
                            objects[trk.objid] = (trk.rect_x1, trk.rect_y1, trk.rect_x2, trk.rect_y2, 
                                trk.classname, trk.elapsed)
                            text.putText(frame, trk.objid, trk.classname, trk.rect_x1, trk.rect_y1, trk.rect_x2, trk.rect_y2)
                            trk = next(tracker)
                            trkr_time = trk.timestamp
                    except StopIteration:
                        trkr_time += timedelta(days=1) # short-circuit any further calls back to the iterator
                        objects = {}

                # draw timestamp on image frame
                tag = f"{format(frame_time.isoformat())}"
                cv2.putText(frame, tag, (30, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1)

                # re-encode the frame back into JPEG format
                #(flag, encodedframe) = cv2.imencode(".jpg", frame)
                encodedframe = simplejpeg.encode_jpeg(frame, quality=95, colorspace='BGR')

                # whenever elapsed time within event > playback elapsed time,
                # estimate a sleep time to dial back the replay framerate
                playback_elaps = datetime.now() - playback_begin
                if frame_elaps > playback_elaps:
                    pause = frame_elaps - playback_elaps
                    time.sleep(pause.seconds + pause.microseconds/1000000)

                # yield the output frame in byte format
                yield(b'--frame\r\nContent-Type: frame/jpeg\r\n\r\n' + 
                    bytearray(encodedframe) + b'\r\n')
        else:
            yield(b'--frame\r\nContent-Type: frame/jpeg\r\n\r\n' + 
                bytearray(create_tiny_jpeg()) + b'\r\n')
    else:
        yield(b'--frame\r\nContent-Type: frame/jpeg\r\n\r\n' + 
            bytearray(create_tiny_jpeg()) + b'\r\n')

@app.route("/video_display/<date>/<event>/<type>")
def video_display(date, event, type):
    return Response(generate_video(date, event, type),
        mimetype = "multipart/x-mixed-replace; boundary=frame")

@app.route("/cam_event/")
@app.route("/cam_event/<date>")
@app.route("/cam_event/<date>/<event>")
@app.route("/cam_event/<date>/<event>/<type>")
def cam_event(date=None, event=None, type=None):
    _event_selection(date, event, type)
    return generate_page()

@app.route("/")
def generate_page():
    _setup_form_data()
    return render_template("event_review.html",
                    date = g.date,
                    event = g.event,
                    type = g.type,
                    node = g.node,
                    view = g.view,
                    start = g.start,
                    datelist = g.datelist,
                    eventlist = g.eventlist)

if __name__ == "__main__":
    # Use a local Flask app when testing
    app.run(host="0.0.0.0", port=8080, debug=True,
		threaded=True, use_reloader=False)
