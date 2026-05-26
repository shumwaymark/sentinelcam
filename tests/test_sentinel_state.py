"""Round-trip tests for sentinel_state.py"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sentinel', 'sentinel'))
from sentinel_state import serialize_state, deserialize_state, save_state, load_state
from datetime import datetime, timedelta

def test_round_trip():
    ts = datetime.now()
    up = timedelta(days=3, hours=14, minutes=22)
    jobs = {
        'abc123': {
            'node': 'east', 'date': '2026-03-17', 'task': 'GetFaces',
            'class': 1, 'sink': 'data1', 'status': 'Done',
            'submitted': ts.isoformat(), 'started': ts.isoformat(),
            'ended': ts.isoformat(), 'elapsed': '0:00:07.123',
            'engine': 'Alpha', 'priority': 1, 'images': 42,
            'rate': 11.5, 'event': 'evt-uuid', 'pump': 'tcp://data1:5556',
            'ring_start_avg': 0.000512, 'ring_next_avg': 0.000089,
        }
    }
    queue = [{'jobid': 'def456', 'event': 'evt2', 'task': 'FaceRecon',
              'priority': 2, 'sink': 'data1', 'node': 'east',
              'date': '2026-03-17', 'pump': 'tcp://data1:5556'}]
    health = {'Alpha': {'total_restarts': 2, 'consecutive_low_frames': 0,
              'consecutive_failures': 0, 'last_restart': ts.isoformat(),
              'job_count': 142}}
    diag = {'start_time': ts.isoformat(), 'engine_busy_total': {'Alpha': 48210.3},
            'queue_hwm': 18, 'queue_latency_hwm': 117.1,
            'queue_by_class_hwm': {'1': 14}, 'submissions_peak_5min': 5,
            'depth_hwm_snapshot_count': 3, 'latency_hwm_snapshot_count': 2}

    json_str = serialize_state(ts, up, jobs, queue, health, diag)
    result = deserialize_state(json_str)
    assert result is not None, 'deserialize returned None'
    assert result['version'] == 1
    assert 'abc123' in result['jobs']
    sub = result['jobs']['abc123']['submitted']
    assert isinstance(sub, datetime), f'Expected datetime, got {type(sub)}'
    lr = result['engine_health']['Alpha']['last_restart']
    assert isinstance(lr, datetime), f'Expected datetime, got {type(lr)}'
    print('Round-trip: PASS')

def test_save_load():
    ts = datetime.now()
    up = timedelta(hours=1)
    jobs = {'j1': {'node': 'east', 'status': 'Done', 'submitted': ts.isoformat()}}
    tmpdir = tempfile.mkdtemp()
    fpath = os.path.join(tmpdir, 'state.json')
    ok = save_state(fpath, ts, up, jobs, [], {}, {})
    assert ok, 'save_state failed'
    assert os.path.exists(fpath), 'state file not found'
    loaded = load_state(fpath)
    assert loaded is not None, 'load_state returned None'
    assert not os.path.exists(fpath), 'state file should be deleted after load'
    assert loaded['version'] == 1
    assert 'j1' in loaded['jobs']
    print('Save/Load: PASS')

def test_missing_file():
    assert load_state('/tmp/nonexistent_state_test.json') is None
    print('Missing file: PASS')

def test_bad_version():
    bad = json.dumps({'version': 99})
    assert deserialize_state(bad) is None
    print('Bad version: PASS')

def test_corrupt_json():
    assert deserialize_state('not json at all{{{') is None
    print('Corrupt JSON: PASS')

if __name__ == '__main__':
    test_round_trip()
    test_save_load()
    test_missing_file()
    test_bad_version()
    test_corrupt_json()
    print('\nAll tests passed.')
