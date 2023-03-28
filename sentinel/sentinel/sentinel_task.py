'''sentinel_task: Example task submission to Sentinel

Copyright (c) 2023 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
'''

import argparse
import json
import zmq
from datetime import datetime

today = datetime.utcnow().isoformat()[:10]
ap = argparse.ArgumentParser()
ap.add_argument("-t", "--task", default="STATUS", help="Task name")
ap.add_argument("-d", "--date", default=today, help="Date (YYYY-MM-DD)")
ap.add_argument("-e", "--event", help="Event ID")
args = vars(ap.parse_args())
event_date = args["date"]
event_id = args["event"]
task_id = args["task"]

request = {'sink': 'data1', 
           'node': 'testMonster',
           'date': event_date, 
           'event': event_id,
           'pump': 'tcp://data1:5556', 
           'task': task_id}

msg = json.dumps(request)

with zmq.Context().socket(zmq.REQ) as sock:
    sock.connect('tcp://sentinel:5566') 
    sock.send(msg.encode("ascii"))
    print(sock.recv().decode("ascii"))
    # Submission request returns 'OK', or 'Error' if submission failed
