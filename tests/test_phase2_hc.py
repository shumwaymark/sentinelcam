"""test_phase2_hc.py — Phase 2 HC command smoke tests.

Tests 2.1 (Sentinel STATUS), 2.2 (CamWatcher HC), 2.3 (DataPump HC).

Usage:
    python test_phase2_hc.py [--sentinel HOST] [--camwatcher HOST] [--datapump HOST]

By default targets the production hostnames from the example YAML configs.
Override per-test with --skip-sentinel / --skip-camwatcher / --skip-datapump.
"""

import argparse
import json
import sys
import zmq
import msgpack


# ---------------------------------------------------------------------------
# 2.1  Sentinel STATUS
# ---------------------------------------------------------------------------

def test_sentinel_status(host: str, port: int = 5566, timeout_ms: int = 8000) -> bool:
    """Send {'task': 'STATUS'} to sentinel control port, validate HC JSON response."""
    addr = f"tcp://{host}:{port}"
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(addr)
    print(f"\n[2.1] Sentinel STATUS → {addr}")
    try:
        request = json.dumps({"task": "STATUS"}).encode("ascii")
        sock.send(request)
        reply = sock.recv()
        data = json.loads(reply.decode("ascii"))

        print(f"  flag      : {data.get('flag')}")
        print(f"  component : {data.get('component')}")
        print(f"  timestamp : {data.get('timestamp')}")
        print(f"  uptime    : {data.get('uptime')}")

        engines = data.get("engines", {})
        if engines:
            for name, eng in engines.items():
                status = eng.get("status", "?")
                accel  = eng.get("accelerator", "?")
                alive  = eng.get("alive", "?")
                health = eng.get("health", {})
                avg_fps = eng.get("avg_fps", 0.0)
                job_count = eng.get("job_count", 0)
                print(f"  engine[{name}]: alive={alive}  accel={accel}  status={status}  "
                      f"restarts={health.get('total_restarts', 0)}  avg_fps={avg_fps}  "
                      f"ring_start={health.get('ring_start_avg', 0.0)}  ring_next={health.get('ring_next_avg', 0.0)}  job_count={job_count}")
        else:
            print("  (no engines in response)")

        queue = data.get("queue", {})
        print(f"  queue     : queued={queue.get('queued', '?')}  running={queue.get('running', '?')}  "
              f"hwm={queue.get('high_water_mark', '?')}")
        print(f"  latency   : current={queue.get('latency_sec', '?')}s  "
              f"hwm={queue.get('latency_hwm_sec', '?')}s")
        if queue.get('oldest_submission'):
            print(f"  oldest    : {queue.get('oldest_submission')}")
        print(f"  completed : {data.get('jobs_completed', '?')}  "
              f"failed: {data.get('jobs_failed', '?')}")

        # Queue diagnostics
        queue_by_class = data.get("queue_by_class", {})
        queue_by_class_hwm = data.get("queue_by_class_hwm", {})
        print(f"  by_class  : current={queue_by_class}  hwm={queue_by_class_hwm}")

        utilization = data.get("utilization_pct", {})
        if utilization:
            for name, pct in utilization.items():
                print(f"  util[{name}]: {pct}%")

        print(f"  submissions_5min: {data.get('submissions_5min', '?')}")

        depth_snaps = data.get("depth_hwm_snapshots", [])
        latency_snaps = data.get("latency_hwm_snapshots", [])
        print(f"  hwm snapshots: depth={len(depth_snaps)}  latency={len(latency_snaps)}")
        if depth_snaps:
            latest = depth_snaps[-1]
            print(f"    latest depth: {latest.get('timestamp')}  "
                  f"depth={latest.get('queue_depth')}  by_task={latest.get('by_task')}")
        if latency_snaps:
            latest = latency_snaps[-1]
            print(f"    latest latency: {latest.get('timestamp')}  "
                  f"latency={latest.get('queue_latency_sec')}s  by_task={latest.get('by_task')}")

        assert data.get("flag") == "HC", "Expected flag='HC'"
        assert data.get("component") == "sentinel", "Expected component='sentinel'"
        assert "engines" in data, "Missing 'engines' key"
        assert "queue_by_class" in data, "Missing 'queue_by_class' key"
        assert isinstance(data.get("utilization_pct"), dict), "Expected 'utilization_pct' to be a dict"
        assert "submissions_5min" in data, "Missing 'submissions_5min' key"
        assert isinstance(data.get("depth_hwm_snapshots"), list), "Expected 'depth_hwm_snapshots' to be a list"
        assert isinstance(data.get("latency_hwm_snapshots"), list), "Expected 'latency_hwm_snapshots' to be a list"
        print("  PASS ✓")
        return True

    except zmq.Again:
        print(f"  FAIL — timeout after {timeout_ms}ms (is sentinel running on {host}?)")
        return False
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"  FAIL — {e}")
        return False
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# 2.2  CamWatcher HC
# ---------------------------------------------------------------------------

def test_camwatcher_hc(host: str, port: int = 5566, timeout_ms: int = 5000) -> bool:
    """Send {'cmd': 'HC'} to camwatcher control port, validate HC JSON response."""
    addr = f"tcp://{host}:{port}"
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(addr)
    print(f"\n[2.2] CamWatcher HC → {addr}")
    try:
        request = json.dumps({"cmd": "HC"}).encode("ascii")
        sock.send(request)
        reply = sock.recv()
        data = json.loads(reply.decode("ascii"))

        print(f"  flag      : {data.get('flag')}")
        print(f"  component : {data.get('component')}")
        print(f"  timestamp : {data.get('timestamp')}")
        print(f"  uptime    : {data.get('uptime')}")

        writers = data.get("writers", {})
        if writers:
            for key, w in writers.items():
                print(f"  writer[{key}]: alive={w.get('alive')}  "
                      f"frames={w.get('frames_written', 0)}  pid={w.get('pid')}")
        else:
            print("  (no writers in response)")

        heartbeats = data.get("heartbeats", {})
        if heartbeats:
            for node, hb in heartbeats.items():
                age = hb.get("age_seconds")
                age_str = f"{age}s ago" if age is not None else "never"
                print(f"  hb[{node}]: fps={hb.get('fps')}  last_seen={age_str}")
        else:
            print("  (no heartbeat data yet)")

        disk = data.get("disk", {})
        if disk:
            print(f"  disk      : {disk.get('used_gb')} / {disk.get('total_gb')} GB  "
                  f"({disk.get('percent')}%)")

        assert data.get("flag") == "HC", "Expected flag='HC'"
        assert data.get("component") == "camwatcher", "Expected component='camwatcher'"
        assert "writers" in data, "Missing 'writers' key"
        print("  PASS ✓")
        return True

    except zmq.Again:
        print(f"  FAIL — timeout after {timeout_ms}ms (is camwatcher running on {host}?)")
        return False
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"  FAIL — {e}")
        return False
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# 2.3  DataPump HC
# ---------------------------------------------------------------------------

def test_datapump_hc(host: str, port: int = 5556, timeout_ms: int = 5000) -> bool:
    """Send msgpack({'cmd': 'HC'}) to datapump, validate HC JSON response."""
    addr = f"tcp://{host}:{port}"
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(addr)
    print(f"\n[2.3] DataPump HC → {addr}")
    try:
        # DataPump receives msgpack-encoded requests (matches datapump.py recv pattern)
        request = msgpack.packb({"cmd": "HC"})
        sock.send(request)
        reply = sock.recv()

        # HC response is a JSON string (not a DataFrame or jpg)
        data = json.loads(reply.decode("ascii") if isinstance(reply, bytes) else reply)

        print(f"  flag                  : {data.get('flag')}")
        print(f"  component             : {data.get('component')}")
        print(f"  timestamp             : {data.get('timestamp')}")
        print(f"  uptime                : {data.get('uptime')}")
        print(f"  requests_served       : {data.get('requests_served', '?')}")
        print(f"  requests_since_hc     : {data.get('requests_since_last_hc', '?')}")
        print(f"  avg_response_ms       : {data.get('avg_response_ms', '?')}")
        print(f"  avg_response_ms_img   : {data.get('avg_response_ms_img', '?')}")
        print(f"  avg_response_ms_other : {data.get('avg_response_ms_other', '?')}")
        print(f"  last_request          : {data.get('last_request')}")

        assert data.get("flag") == "HC", "Expected flag='HC'"
        assert data.get("component") == "datapump", "Expected component='datapump'"
        assert "requests_served" in data, "Missing 'requests_served' key"
        print("  PASS ✓")
        return True

    except zmq.Again:
        print(f"  FAIL — timeout after {timeout_ms}ms (is datapump running on {host}?)")
        return False
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"  FAIL — {e}")
        return False
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Phase 2 HC command smoke tests")
    parser.add_argument("--sentinel",   default="sentinel", metavar="HOST",
                        help="Sentinel host (default: sentinel)")
    parser.add_argument("--camwatcher", default="data1",    metavar="HOST",
                        help="CamWatcher host (default: data1)")
    parser.add_argument("--datapump",   default="data1",    metavar="HOST",
                        help="DataPump host (default: data1)")
    parser.add_argument("--skip-sentinel",   action="store_true")
    parser.add_argument("--skip-camwatcher", action="store_true")
    parser.add_argument("--skip-datapump",   action="store_true")
    args = parser.parse_args()

    results = {}

    if not args.skip_sentinel:
        results["2.1 sentinel STATUS"] = test_sentinel_status(args.sentinel)

    if not args.skip_camwatcher:
        results["2.2 camwatcher HC"] = test_camwatcher_hc(args.camwatcher)

    if not args.skip_datapump:
        results["2.3 datapump HC"] = test_datapump_hc(args.datapump)

    print("\n" + "─" * 50)
    passed = sum(1 for v in results.values() if v)
    total  = len(results)
    for label, ok in results.items():
        mark = "PASS ✓" if ok else "FAIL ✗"
        print(f"  {mark}  {label}")
    print(f"\n  {passed}/{total} passed")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
