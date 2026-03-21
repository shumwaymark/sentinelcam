'''sentinel_adhoc: Register an ad-hoc sentinel server with a camwatcher.

Copyright (c) 2025 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
'''
import json
import zmq

DATASINK = 'data1'
SENTINEL = 'sentinel2'

request = {'cmd': 'Agent',
           'name': SENTINEL,
           'requests': f'tcp://{SENTINEL}:5566',
           'publisher': f'tcp://{SENTINEL}:5565',
           'datapump': f'tcp://{DATASINK}:5556',
           'datasink': DATASINK}

msg = json.dumps(request)

with zmq.Context.instance().socket(zmq.REQ) as sock:
    sock.connect(f'tcp://{DATASINK}:5566')
    sock.send(msg.encode("ascii"))
    print(sock.recv().decode("ascii"))
