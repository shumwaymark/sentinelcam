"""sentinel_state: State serialization for Sentinel checkpoint and recovery.

Pure functions. No dependency on JobManager, TaskEngine, or any sentinel.py
internals. Called from JobManager._run_maintenance(), _jobThread incremental
checkpoints, and main() startup recovery.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import json
import os
import logging
from datetime import datetime, timedelta

STATE_VERSION = 1

# Datetime fields that need ISO↔datetime conversion in job records
_JOB_DATETIME_FIELDS = ('submitted', 'started', 'ended')


def _json_default(obj):
    """JSON serializer for types not natively supported."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def serialize_state(timestamp, uptime, job_records, queue_records,
                    engine_health, diagnostics) -> str:
    """Serialize sentinel state to a JSON string.

    Args:
        timestamp: datetime of the checkpoint
        uptime: timedelta since sentinel start
        job_records: dict of {jobID: job_field_dict} — each dict contains
            the serializable fields from a JobRequest
        queue_records: list of dicts for Queued/on-deck/Running jobs
        engine_health: dict of {engineName: health_dict}
        diagnostics: dict of period accumulator values

    Returns:
        JSON string suitable for writing to state file.
    """
    state = {
        'version': STATE_VERSION,
        'timestamp': timestamp.isoformat(),
        'sentinel_uptime': str(uptime),
        'jobs': job_records,
        'queue': queue_records,
        'engine_health': engine_health,
        'diagnostics': diagnostics,
    }
    return json.dumps(state, default=_json_default)


def deserialize_state(json_str) -> dict:
    """Deserialize a JSON state string to a working dict.

    Returns:
        dict with keys: version, timestamp, sentinel_uptime,
        jobs, queue, engine_health, diagnostics.
        Datetime strings in job records are converted back to datetime objects.
        Returns None if version is unsupported.
    """
    try:
        state = json.loads(json_str)
    except (json.JSONDecodeError, TypeError) as e:
        logging.warning(f"Failed to parse state JSON: {e}")
        return None
    if state.get('version') != STATE_VERSION:
        logging.warning(f"Unsupported state file version: {state.get('version')} "
                        f"(expected {STATE_VERSION})")
        return None
    # Convert top-level timestamp
    if 'timestamp' in state and state['timestamp']:
        state['timestamp'] = datetime.fromisoformat(state['timestamp'])
    # Convert datetime fields in job records
    for job in state.get('jobs', {}).values():
        for field in _JOB_DATETIME_FIELDS:
            if field in job and job[field] is not None:
                job[field] = datetime.fromisoformat(job[field])
    # Convert last_restart in engine health records
    for health in state.get('engine_health', {}).values():
        if health.get('last_restart') is not None:
            health['last_restart'] = datetime.fromisoformat(health['last_restart'])
        # The rolling restart budget is a trailing time window; without this the
        # ledger comes back as bare strings, is filtered out on restore, and every
        # sentinel restart hands each engine a fresh full budget.
        if health.get('restart_times'):
            health['restart_times'] = [
                datetime.fromisoformat(t) if isinstance(t, str) else t
                for t in health['restart_times']]
    # Convert diagnostics start_time
    diag = state.get('diagnostics', {})
    if diag.get('start_time') is not None:
        diag['start_time'] = datetime.fromisoformat(diag['start_time'])
    return state


def save_state(filepath, timestamp, uptime, job_records, queue_records,
               engine_health, diagnostics) -> bool:
    """Atomic write of serialized state to disk.

    Writes to a temp file in the same directory, then renames.
    Returns True on success, False on failure (logged, never raises).
    """
    tmppath = filepath + '.tmp'
    try:
        json_str = serialize_state(timestamp, uptime, job_records,
                                   queue_records, engine_health, diagnostics)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(tmppath, 'w') as f:
            f.write(json_str)
        os.replace(tmppath, filepath)
        logging.debug(f"State checkpoint saved: {filepath}")
        return True
    except OSError as e:
        logging.error(f"Failed to save state checkpoint: {e}")
        # Clean up temp file if rename failed
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        return False
    except Exception as e:
        logging.error(f"Unexpected error saving state checkpoint: {e}")
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        return False


def load_state(filepath) -> dict:
    """Load state file and delete it after successful read.

    Returns parsed state dict, or None if file is missing, corrupt,
    or has an unsupported version.
    """
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r') as f:
            json_str = f.read()
        state = deserialize_state(json_str)
        if state is None:
            logging.warning(f"State file rejected (unsupported version): {filepath}")
            return None
        os.unlink(filepath)
        logging.info(f"Loaded state checkpoint: {filepath} "
                     f"({len(state.get('jobs', {}))} jobs, "
                     f"{len(state.get('queue', []))} queued)")
        return state
    except json.JSONDecodeError as e:
        logging.warning(f"Corrupt state file {filepath}: {e}")
        return None
    except OSError as e:
        logging.warning(f"Error reading state file {filepath}: {e}")
        return None
