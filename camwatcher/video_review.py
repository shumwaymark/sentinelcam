""" video_review: A trivial Flask application to demonstrate video event review """
import time
from datetime import date, datetime, timedelta
from flask import Flask
from flask import Response
from flask import render_template, g
import cv2
import simplejpeg
from camwatcher.camdata import CamData

app = Flask(__name__) # initialize a flask object

cfg = {'port': 8080, 
       'address': '0.0.0.0',
       'imagefolder': '/mnt/usb1/imagedata/video',
       'datafolder':  '/mnt/usb1/imagedata/camwatcher'} 

@app.before_request
def before_request():
    g.cwData = CamData(cfg["datafolder"], cfg["imagefolder"])
    g.date = g.cwData.get_date()
    g.event = None

def _event_selection(date=None, event=None):
    if date is None:
        g.date = datetime.utcnow().isoformat()[:10]
    else:
        g.date = date
    g.cwData.set_date(g.date)
    if event is None:
        g.event = g.cwData.get_last_event()
    else:
        g.event = event
    g.cwData.set_event(g.event)

def _generate_event_list(cindx):
    for row in cindx[:].itertuples():
        yield (row.event, row.timestamp.strftime("%H:%M:%S") + " " +
                          row.node + " " + 
                          row.viewname)

def _setup_form_data():
    if not g.event: 
        _event_selection()    
    g.node = g.cwData.get_event_node()
    g.view = g.cwData.get_event_view()
    g.start = g.cwData.get_event_start()
    g.datelist = [(d, date.fromisoformat(d).strftime('%A %B %d, %Y')) for d in g.cwData.get_date_list()]
    g.eventlist = [(evt, descr) for (evt, descr) in _generate_event_list(g.cwData.get_index())]

def _get_frametime(pathname):
    return datetime.strptime(pathname[-30:-4],"%Y-%m-%d_%H.%M.%S.%f")

def generate_video(date, event):
    color = (0,255,0)
    _cwData = CamData(cfg["datafolder"], cfg["imagefolder"], date)
    _cwData.set_event(event)
    tracker = _cwData.get_event_data()[:].itertuples()
    image_list = _cwData.get_event_images()
    event_start = _cwData.get_event_start()
    objects = {}  # object dictionary for holding last known coordinates
    trk = next(tracker)
    iter_elapsed = trk.elapsed
    playback_begin = datetime.utcnow()
    for framepath in image_list:
        frame = cv2.imread(framepath)
        frame_time = _get_frametime(framepath) 
        frame_elaps = frame_time - event_start
        if iter_elapsed < frame_elaps:
            try:
                while trk.elapsed < frame_elaps:
                    objects[trk.objid] = (trk.centroid_x, trk.centroid_y, trk.elapsed)
                    trk = next(tracker)
                iter_elapsed = trk.elapsed
            except StopIteration:
                iter_elapsed = timedelta(days=1) # short-circuit any further calls back to the iterator

        for (objid, (centx, centy, lastknown)) in objects.items():
            # draw both the ID and centroid of the object on the output frame
            label = "ID {}".format(objid)
            cv2.putText(frame, label, (centx - 10, centy - 10), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
            cv2.circle(frame, (centx, centy), 4, color, -1)

        # draw timestamp on image frame
        tag = "{} UTC".format(framepath[-30:-4].replace('_',' '))
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
