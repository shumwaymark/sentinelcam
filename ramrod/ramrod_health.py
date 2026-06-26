#!/usr/bin/env python3
"""ramrod_health: Ramrod Health Observer — collect and consolidate system health.

Queries CamWatcher, DataPump, and Sentinel for health check responses,
assembles a SYSHEALTH report, and delivers it to Sentinel for re-broadcast
to all log subscribers (including Watchtower).

Designed to run every 3 minutes via cron on buzz (Pi, pi user).
Each invocation is stateless — no persistence between runs.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import msgpack
import yaml
import zmq


class HealthChecker:
    """Collects health check responses from all SentinelCam components."""

    def __init__(self, config):
        self.config = config
        self.timeout = config.get('timeout_ms', 3000)

    def _zmq_request(self, address, payload):
        """Send a ZMQ REQ and return the response string, or None on failure.

        Each call creates and tears down its own context/socket to avoid
        stale socket state between cron invocations.
        """
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REQ)
        sock.setsockopt(zmq.LINGER, 1000)
        sock.setsockopt(zmq.RCVTIMEO, self.timeout)
        sock.setsockopt(zmq.SNDTIMEO, self.timeout)
        try:
            sock.connect(address)
            sock.send(payload.encode('ascii'))
            reply = sock.recv().decode('ascii')
            return reply
        except zmq.ZMQError as e:
            logging.warning(f"ZMQ error talking to {address}: {e}")
            return None
        finally:
            sock.close()
            ctx.term()

    def _msgpack_request(self, address, request_dict):
        """Send a msgpack-encoded ZMQ REQ and return the response string.

        Used for DataPump which speaks the ImageHub/msgpack protocol.
        Request is msgpack-encoded dict, response is decoded as ASCII string.
        Returns the response string, or None on failure.
        """
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REQ)
        sock.setsockopt(zmq.LINGER, 1000)
        sock.setsockopt(zmq.RCVTIMEO, self.timeout)
        sock.setsockopt(zmq.SNDTIMEO, self.timeout)
        try:
            sock.connect(address)
            sock.send(msgpack.dumps(request_dict))
            reply = sock.recv().decode('ascii')
            return reply
        except zmq.ZMQError as e:
            logging.warning(f"ZMQ error talking to {address}: {e}")
            return None
        finally:
            sock.close()
            ctx.term()

    def check_camwatcher(self):
        """Query CamWatcher HC endpoint. Returns parsed dict or error dict."""
        address = self.config['camwatcher_address']
        payload = json.dumps({'cmd': 'HC'})
        logging.debug(f"Checking CamWatcher at {address}")
        reply = self._zmq_request(address, payload)
        if reply is None:
            return {'ok': False, 'error': 'no response', 'address': address}
        try:
            result = json.loads(reply)
            result['ok'] = True
            return result
        except (json.JSONDecodeError, ValueError) as e:
            return {'ok': False, 'error': f'bad response: {e}', 'raw': reply[:200]}

    def check_datapump(self):
        """Query DataPump HC endpoint. Returns parsed dict or error dict.

        DataPump uses the ImageHub/msgpack protocol, not JSON.
        Request must be msgpack-encoded; response is an ASCII JSON string.
        """
        address = self.config['datapump_address']
        logging.debug(f"Checking DataPump at {address}")
        reply = self._msgpack_request(address, {'cmd': 'HC'})
        if reply is None:
            return {'ok': False, 'error': 'no response', 'address': address}
        try:
            result = json.loads(reply)
            result['ok'] = True
            return result
        except (json.JSONDecodeError, ValueError) as e:
            return {'ok': False, 'error': f'bad response: {e}', 'raw': reply[:200]}

    def check_sentinel(self):
        """Query Sentinel STATUS endpoint. Returns parsed dict or error dict."""
        address = self.config['sentinel_address']
        payload = json.dumps({'task': 'STATUS'})
        logging.debug(f"Checking Sentinel at {address}")
        reply = self._zmq_request(address, payload)
        if reply is None:
            return {'ok': False, 'error': 'no response', 'address': address}
        if reply == 'Error':
            return {'ok': False, 'error': 'sentinel returned Error'}
        try:
            result = json.loads(reply)
            result['ok'] = True
            return result
        except (json.JSONDecodeError, ValueError) as e:
            return {'ok': False, 'error': f'bad response: {e}', 'raw': reply[:200]}

    def derive_outpost_health(self, camwatcher_result):
        """Derive per-outpost health from CamWatcher HC response.

        CamWatcher reports writer status and heartbeat info for each outpost.
        We consolidate this into per-outpost ok/not-ok with details.
        """
        outposts = {}
        if not camwatcher_result.get('ok'):
            # CamWatcher is down — all outposts are unknown
            for name in self.config.get('outposts', []):
                outposts[name] = {'ok': None, 'error': 'camwatcher unavailable'}
            return outposts

        writers = camwatcher_result.get('writers', {})
        crop_writers = camwatcher_result.get('crop_writers', {})
        heartbeats = camwatcher_result.get('heartbeats', {})

        for name in self.config.get('outposts', []):
            status = {'ok': True}
            # Check writer
            writer_key = None
            for wk in writers:
                if wk.startswith(name + '/') or wk == name:
                    writer_key = wk
                    break
            if writer_key:
                w = writers[writer_key]
                status['writer_alive'] = w.get('alive', False)
                status['frames_written'] = w.get('frames_written', 0)
                if not w.get('alive', False):
                    status['ok'] = False
            else:
                status['writer_alive'] = None

            # Crop writer — OAK crop-publishing nodes only. Mirror the writer
            # key match; non-OAK nodes have no crop_writers entry, so these
            # keys are simply absent. crops_written (image plane) vs crp_records
            # (log plane); a persistent gap signals crop/crp pair failures.
            crop_key = None
            for ck in crop_writers:
                if ck.startswith(name + '/') or ck == name:
                    crop_key = ck
                    break
            if crop_key:
                cw = crop_writers[crop_key]
                status['crop_writer_alive'] = cw.get('alive', False)
                status['crops_written'] = cw.get('crops_written', 0)
                status['crp_records'] = cw.get('crp_records', 0)

            # Check heartbeat
            hb = heartbeats.get(name)
            if hb:
                status['last_heartbeat'] = hb.get('last_seen')
                status['fps'] = hb.get('fps')
                age = hb.get('age_seconds')
                status['heartbeat_age'] = age
                # Stale if no heartbeat in 10 minutes (600s)
                if age is not None and age > 600:
                    status['ok'] = False
                    status['stale'] = True
            else:
                # No heartbeat recorded — the outpost has not checked in since
                # camwatcher started. The ImageStreamWriter subprocess stays alive
                # even when an outpost is offline, so writer_alive alone is not a
                # reliable liveness signal. Mark as unknown/stale rather than OK.
                status['last_heartbeat'] = None
                status['ok'] = None
                status['stale'] = True

            outposts[name] = status

        return outposts

    def collect(self):
        """Run all health checks and assemble SYSHEALTH report."""
        now = datetime.now()

        camwatcher = self.check_camwatcher()
        datapump = self.check_datapump()
        sentinel = self.check_sentinel()
        outposts = self.derive_outpost_health(camwatcher)

        report = {
            'flag': 'SYSHEALTH',
            'timestamp': now.isoformat(),
            'camwatcher': camwatcher,
            'datapump': datapump,
            'sentinel': sentinel,
            'outposts': outposts,
        }

        return report

    def deliver(self, report):
        """Submit SYSHEALTH report to Sentinel via HEALTH_REPORT command."""
        address = self.config['sentinel_address']
        payload = json.dumps({'task': 'HEALTH_REPORT', 'payload': report})
        logging.debug(f"Delivering SYSHEALTH to Sentinel at {address}")
        reply = self._zmq_request(address, payload)
        if reply is None:
            logging.error(f"Failed to deliver SYSHEALTH to Sentinel at {address}")
            return False
        if reply == 'OK':
            logging.info("SYSHEALTH report delivered to Sentinel")
            return True
        else:
            logging.error(f"Sentinel replied: {reply}")
            return False


def load_config(config_path):
    """Load ramrod.yaml configuration."""
    path = Path(config_path)
    if not path.exists():
        print(f"Config file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return yaml.safe_load(f)


def setup_logging(log_path):
    """Configure logging with RotatingFileHandler."""
    log_dir = Path(log_path).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def main():
    parser = argparse.ArgumentParser(
        description='Ramrod Health Observer — SentinelCam system health check')
    parser.add_argument('--config', '-c',
        default=str(Path.home() / 'ramrod' / 'ramrod.yaml'),
        help='Path to ramrod.yaml config file')
    parser.add_argument('--dry-run', '-n', action='store_true',
        help='Collect health data and print report without delivering to Sentinel')
    parser.add_argument('--verbose', '-v', action='store_true',
        help='Enable debug logging')
    args = parser.parse_args()

    cfg = load_config(args.config)

    log_path = cfg.get('log_file', str(Path.home() / 'ramrod' / 'logs' / 'ramrod_health.log'))
    setup_logging(log_path)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    checker = HealthChecker(cfg)
    report = checker.collect()

    if args.dry_run:
        print(json.dumps(report, indent=2))
        sys.exit(0)

    success = checker.deliver(report)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
