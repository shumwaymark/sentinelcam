'''sentinel_task: Task submission protocol to Sentinel

Copyright (c) 2023 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
'''

import argparse
import json
import zmq
from datetime import date, timedelta

DATASINK = 'data1'
yesterday = str(date.today() - timedelta(days=1))

ap = argparse.ArgumentParser()
ap.add_argument("-t", "--task", default="STATUS", help="Task name")
ap.add_argument("-d", "--date", default=yesterday, help="Date (YYYY-MM-DD)")
ap.add_argument("-e", "--event", help="Event ID")
ap.add_argument("-s", "--sink", default=DATASINK, help="Data sink host name")
args = vars(ap.parse_args())

task_id = args["task"]
event_date = args["date"]
event_id = args["event"]
datasink = args["sink"] 

request = {'task': task_id,
           'date': event_date, 
           'event': event_id,
           'sink': datasink, 
           'node': 'injected_task',
           'pump': f'tcp://{datasink}:5556'}
           
msg = json.dumps(request)

with zmq.Context.instance().socket(zmq.REQ) as sock:
    sock.connect('tcp://localhost:5566') 
    sock.send(msg.encode("ascii"))
    print(sock.recv().decode("ascii"))

# Submission request returns either the JobID or 'OK'. 
# Will return 'Error' if submission failed; details in sentinel log.
