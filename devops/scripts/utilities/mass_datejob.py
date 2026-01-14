import argparse 
import json
import zmq
from sentinelcam.datafeed import DataFeed

ap = argparse.ArgumentParser()
ap.add_argument("-d", "--date", required=True, help="Date (YYYY-MM-DD)")
ap.add_argument("-d2", "--date2", required=True, help="End date to define range (YYYY-MM-DD)")
ap.add_argument("-t", "--task", required=True, help="Task name")
args = vars(ap.parse_args())

feed = DataFeed('tcp://data1:5556')
sentinel = feed.zmq_context.socket(zmq.REQ)
sentinel.connect('tcp://sentinel:5566') 

all_dates = feed.get_date_list()
all_dates.reverse()
datelist = [d for d in all_dates if d >= args['date'] and d <= args['date2']]

cnt = 0
for day in datelist:
    request = {'task': args['task'],
                'date': day,
                'event': None,
                'sink': 'data1', 
                'node': ['lab','workbench'],
                'pump': 'tcp://data1:5556'}
    msg = json.dumps(request)
    sentinel.send(msg.encode("ascii"))
    jobid = sentinel.recv().decode("ascii")
    print(day, jobid)
    cnt += 1

print(args['task'], cnt)
sentinel.close()
feed.close()
