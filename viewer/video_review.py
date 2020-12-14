import os
import time
import sys
from datetime import datetime, timezone
from flask import Flask
from flask import request
from flask import Response
from flask import render_template
import psycopg2
import cv2

cfg = {'port': 8080, 
       'address': '0.0.0.0',
       'basefolder': '/mnt/usb1/imagedata/video',
       'dbconn': 'postgresql://sentinelcam:sentinelcam@data1./sentinelcam'} # DBMS connection string 

SQL = """
SELECT object_tag, 
       EXTRACT(SECONDS FROM object_time - start_time) AS elaps,
       centroid_x,
       centroid_y
  FROM cam_tracking
 WHERE node_name = '{}'
   AND view_name = '{}'
   AND start_time = (
      SELECT start_time
        FROM cam_event
       WHERE node_name = cam_tracking.node_name
         AND view_name = cam_tracking.view_name
         AND date(start_time) = '{}'
         AND pipe_event = {} )
 ORDER BY object_time """

app = Flask(__name__) # initialize a flask object

def list_frames(ymd, node, view, eventid):
    # return the set of files that captured the event
    datefolder = os.path.join(cfg["basefolder"], ymd)
    jpegbase = '_'.join([node, view, str(eventid).zfill(5)])
    return list_framefiles(datefolder, jpegbase)

def list_framefiles(basePath, prefix):
    # loop over the directory structure
    with os.scandir(basePath) as framefiles:
        # loop over entries in the base directory
        for framefile in framefiles:
            # only produce file entries with a matching prefix
            # yield tuple with pathname, file modifaction timestamp
            if framefile.name.startswith(prefix):
                info = framefile.stat()
                yield (framefile.path, 
                       datetime.fromtimestamp(info.st_mtime, timezone.utc))

def convert_date(timestamp):
    return timestamp.strftime('%d %b %Y %I:%M:%S %p %Z')

# function to extract frame number from the frame pathname
def get_number(frameFrame):
    return int(frameFrame[0][-14:-4])

def generate(ymd, node, view, eventID):
    color = (0,255,0)

    # query event tracking data from database
    with psycopg2.connect(cfg['dbconn']) as conn:
        with conn.cursor() as curs:
            curs.execute(SQL.format(node,view,ymd,eventID))
            objlist = curs.fetchall()

    objects = {}  # object dictionary for holding last known coordinates
    tracker = iter(objlist) # iterator for object tracking query result set

    # grab sorted list of video frame tuples (pathname, timestamp)
    videoframes = sorted(list(list_frames(ymd, node, view, eventID)),
                         key=get_number)
    begintime = datetime.now()
    eventstart = videoframes[0][1]

    (objid, elaps, centx, centy) = next(tracker)
    for (framepath, capturetime) in videoframes:
	
        frame = cv2.imread(framepath)
        frametime = capturetime - eventstart
        thisframe = frametime.seconds + frametime.microseconds/1000000
        if elaps < thisframe:
            try:
                while elaps < thisframe:
                    objects[objid] = (centx, centy, elaps)
                    (objid, elaps, centx, centy) = next(tracker)
            except StopIteration:
                elaps = 86400.0 # short-cicuit any futher calls back to the interator

        for (objid, (centx, centy, lastknown)) in objects.items():
            # draw both the ID and centroid of the object on the output frame
            label = "ID {}".format(objid)
            cv2.putText(frame, label, (centx - 10, centy - 10), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
            cv2.circle(frame, (centx, centy), 4, color, -1)

        # draw timestamp on image frame
        tag = "{}".format(convert_date(capturetime))
        cv2.putText(frame, tag, (30, 450),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        # re-encode the frame back into JPEG format
        (flag, encodedframe) = cv2.imencode(".jpg", frame)

        # ensure the frame was successfully encoded
        if not flag:
            continue
            
        # whenever elapsed time within event > playback elapsed time,
        # estimate a sleep time to dial back the replay framerate
        present = datetime.now() - begintime
        if (frametime > present):
            pause = frametime - present
            time.sleep(pause.seconds + pause.microseconds/1000000)

        # yield the output frame in byte format
        yield(b'--frame\r\nContent-Type: frame/jpeg\r\n\r\n' + 
            bytearray(encodedframe) + b'\r\n')

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/video_sample")
def video_sample():
    return Response(generate("2020-11-22", "outpost", "PiCamera", 213),
        mimetype = "multipart/x-mixed-replace; boundary=frame")

@app.route("/replay_event")
def replay_event():
    yyyy = int(request.args.get("year"))
    mm = int(request.args.get("month"))
    dd = int(request.args.get("day"))
    node = request.args.get("node")
    view = request.args.get("view")
    eventid = request.args.get("event")
    eventdate = datetime(yyyy,mm,dd)
    ymd = eventdate.isoformat()[:10] # this is "YYYY-MM-DD" format
    #print(f"yyyy={yyyy} mm={mm} dd={dd} node={node} view={view} eventid={eventid}")
    return Response(generate(ymd, node, view, eventid),
        mimetype = "multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
	# start the flask app
	app.run(host=cfg["address"], port=cfg["port"], debug=True,
		threaded=True, use_reloader=False)
