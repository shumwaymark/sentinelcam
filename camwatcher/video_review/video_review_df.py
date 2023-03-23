"""   video_review:     A trivial Flask application to demonstrate video event review.
                        This uses the CamData class for access to camwatcher data. 
      video_review_df:  Revised/enhanced version using datafeed to datapump access for
                        operation from within a WSGI environment on an application server.  """
import time
from datetime import date, datetime, timedelta
from flask import Flask
from flask import Response
from flask import render_template, g
import cv2
import numpy as np
import simplejpeg
from datafeed import DataFeed

app = Flask(__name__) # initialize a flask object

cfg = {'datapump': 'tcp://data1:5556'} 

@app.before_request
def before_request():
    g.cwFeed = DataFeed(cfg["datapump"])
    g.cwIndx = None
    g.cwEvt = None
    g.date = None
    g.event = None

def _event_selection(date=None, event=None):
    if date is None:
        g.date = g.cwFeed.get_date_list()[0]
    else:
        g.date = date
    g.cwIndx = g.cwFeed.get_date_index(g.date)        
    if event is None:
        g.event = g.cwIndx.iloc[0].event
    else:
        g.event = event
    g.cwEvt = g.cwFeed.get_tracking_data(g.date, g.event)

def _generate_event_list(cindx):
    for row in cindx[:].itertuples():
        yield (row.event, row.timestamp.strftime("%H:%M:%S") + " " +
                          row.node + " " + 
                          row.viewname)

def _setup_form_data():
    if not g.event: 
        _event_selection()
    indxData = g.cwIndx.loc[g.cwIndx["event"] == g.event].iloc[0]
    g.node = indxData.node
    g.view = indxData.viewname
    g.start = indxData.timestamp
    g.datelist = [(d, date.fromisoformat(d).strftime('%A %B %d, %Y')) for d in g.cwFeed.get_date_list()]
    g.eventlist = [(evt, descr) for (evt, descr) in _generate_event_list(g.cwIndx)]

#def _get_frametime(pathname):
#    return datetime.strptime(pathname[-30:-4],"%Y-%m-%d_%H.%M.%S.%f")

class TextHelper:
    def __init__(self, camevt) -> None:
        self._textColor = (0, 0, 0)
        self._lineType = cv2.LINE_AA
        self._textType = cv2.FONT_HERSHEY_SIMPLEX
        self._bboxColors = {}        
        for objid in camevt['objid'].unique():
            self._bboxColors[objid] = np.random.randint(256, size=3)
    def putText(self, frame, objid, text, x1, y1, x2, y2):
        color = tuple(int(x) for x in self._bboxColors[objid])
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.rectangle(frame, (x1, (y1 - 28)), ((x1 + 110), y1), color, cv2.FILLED)
        cv2.putText(frame, text, (x1 + 5, y1 - 10), self._textType, 0.5, self._textColor, 1, self._lineType)

def generate_video(date, event):
    color = (0,255,0)
    _cwFeed = DataFeed(cfg["datapump"])
    _cwEvt = _cwFeed.get_tracking_data(date, event)
    text = TextHelper(_cwEvt)
    tracker = _cwEvt[:].itertuples()
    image_list = _cwFeed.get_image_list(date, event)
    event_start = _cwEvt.iloc[0].timestamp
    objects = {}  # object dictionary for holding last known coordinates
    trk = next(tracker)
    iter_elapsed = trk.elapsed
    playback_begin = datetime.utcnow()
    for frame_time in image_list:
        jpeg = _cwFeed.get_image_jpg(date, event, frame_time)
        frame = simplejpeg.decode_jpeg(jpeg, colorspace='BGR')
        frame_elaps = frame_time - event_start
        if iter_elapsed < frame_elaps:
            try:
                while trk.elapsed < frame_elaps:
                    objects[trk.objid] = (trk.rect_x1, trk.rect_y1, trk.rect_x2, trk.rect_y2, 
                        trk.classname, trk.elapsed)
                    trk = next(tracker)
                iter_elapsed = trk.elapsed
            except StopIteration:
                iter_elapsed = timedelta(days=1) # short-circuit any further calls back to the iterator
                objects = {}

        for (objid, (rect_x1, rect_y1, rect_x2, rect_y2, classname, lastknown)) in objects.items():
            # draw last known object tracking data on the output frame
            text.putText(frame, objid, classname, rect_x1, rect_y1, rect_x2, rect_y2)

        # draw timestamp on image frame
        tag = "{} UTC".format(frame_time.isoformat())
        cv2.putText(frame, tag, (30, 450),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        # re-encode the frame back into JPEG format
        #(flag, encodedframe) = cv2.imencode(".jpg", frame)
        encodedframe = simplejpeg.encode_jpeg(frame, 
            quality=95, colorspace='BGR')

        # whenever elapsed time within event > playback elapsed time,
        # estimate a sleep time to dial back the replay framerate
        playback_elaps = datetime.utcnow() - playback_begin
        if frame_elaps > playback_elaps:
            pause = frame_elaps - playback_elaps
            time.sleep(pause.seconds + pause.microseconds/1000000)

        # yield the output frame in byte format
        yield(b'--frame\r\nContent-Type: frame/jpeg\r\n\r\n' + 
            bytearray(encodedframe) + b'\r\n')

@app.route("/video_display/<date>/<event>")
def video_display(date, event):
    return Response(generate_video(date, event),
        mimetype = "multipart/x-mixed-replace; boundary=frame")

@app.route("/cam_event/")
@app.route("/cam_event/<date>")
@app.route("/cam_event/<date>/<event>")
def cam_event(date=None, event=None):
    _event_selection(date, event)
    return index()

@app.route("/")
def index():
    _setup_form_data()
    return render_template("index.html",
                    date = g.date,
                    event = g.event,
                    node = g.node,
                    view = g.view,
                    start = g.start,
                    datelist = g.datelist,
                    eventlist = g.eventlist)

if __name__ == "__main__":
    # start the flask app
    app.run(host="0.0.0.0", port=8080, debug=True,
		threaded=True, use_reloader=False)
