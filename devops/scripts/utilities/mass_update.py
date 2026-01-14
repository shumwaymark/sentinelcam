import argparse 
import json
import zmq
from datetime import date
from sentinelcam.datafeed import DataFeed, EventList

ap = argparse.ArgumentParser()
ap.add_argument("-d", "--date", default=str(date.today()), help="Date (YYYY-MM-DD)")
ap.add_argument("-d2", "--date2", help="End date to define range (YYYY-MM-DD)")
ap.add_argument("-f", "--filename", help="Optional file of dates and events")
ap.add_argument("-t", "--task", default="MobileNetSSD_allFrames", help="Task name")
ap.add_argument("-r", "--trk", default="trk", help="Task tracking type reference")
args = vars(ap.parse_args())

feed = DataFeed('tcp://data1.:5556')
sentinel = feed.zmq_context.socket(zmq.REQ)
sentinel.connect('tcp://sentinel.:5566') 

events = EventList(feed=feed, date1=args['date'], date2=args['date2'], filename=args['filename'], trk=args['trk'])
event_list = events.get_event_list()

for (day,event) in event_list:
    request = {'task': args['task'],
                'date': day,
                'event': event,
                'sink': 'data1', 
                'node': ['lab','workbench'],
                'pump': 'tcp://data1:5556'}
    msg = json.dumps(request)
    sentinel.send(msg.encode("ascii"))
    jobid = sentinel.recv().decode("ascii")

sentinel.close()
feed.close()
