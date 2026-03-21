#!/usr/bin/env python3
"""sentinel_maintenance: Trigger sentinel maintenance cycle.

Sends a MAINTENANCE command to the local sentinel control port.
Run from systemd timer (daily at 01:00).

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import json
import zmq

with zmq.Context.instance().socket(zmq.REQ) as sock:
    sock.connect('tcp://localhost:5566')
    sock.send(json.dumps({'task': 'MAINTENANCE'}).encode('ascii'))
    print(sock.recv().decode('ascii'))
