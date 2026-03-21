#!/usr/bin/env python3
"""sentinel_shutdown: Initiate graceful sentinel shutdown.

Sends a SHUTDOWN command to the local sentinel control port.
The sentinel will drain running tasks (waiting for completion or timeout),
serialize state to disk, terminate engine child processes, and exit.

Can be run manually or integrated into deployment scripts.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import json
import zmq

with zmq.Context.instance().socket(zmq.REQ) as sock:
    sock.connect('tcp://localhost:5566')
    sock.send(json.dumps({'task': 'SHUTDOWN'}).encode('ascii'))
    reply = sock.recv().decode('ascii')
    print(f"Sentinel: {reply}")
