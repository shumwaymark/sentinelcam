# Plan: Ramrod Health Observer — Phase 5 Development

Replace the sketchy Phase 5 in the health monitoring plan with a concrete implementation plan. Ramrod is an independent observer on buzz (Pi, `pi` user, 192.168.10.10) — NOT a sentinelcam component. No systemd service, no code_deployment pipeline, not in Ansible inventory. Runs as a cron-invoked script that collects HC responses from all components, assembles a SYSHEALTH report, and delivers it to sentinel for re-broadcast.

## Key Design Decisions

- **D-R1: Deployment.** Source at `ramrod/` top-level. Syncs to buzz via extended `sync-ramrod-from-datasink.sh`. NOT in `code_deployment.components`. One-time bootstrap via shell script.
- **D-R2: Config.** Jinja2 template in a minimal Ansible role renders `ramrod.yaml` from inventory vars. Rendered locally on buzz: `ansible-playbook playbooks/configure-ramrod.yaml --connection=local`.
- **D-R3: Python env.** Dedicated venv at `/home/pi/ramrod/venv`. Deps: `pyzmq`, `pyyaml` only.
- **D-R4: Outpost health.** CamWatcher HC is the primary source (heartbeats, writer frame counts). No direct image SUB checks in v1 — future scope for failure confirmation.
- **D-R5: Scope.** Observer only. Collect, consolidate, publish. No recovery, no persistent state.
- **D-R6: Self-management.** buzz not in inventory. Bootstrap manually. Config playbook targets `localhost` with `connection: local`.

---

## Steps

### Phase A: Sentinel HEALTH_REPORT command *(sentinel.py)*

1. Add `HEALTH_REPORT` handler in `task_loop` — identical to existing ALERT pattern: extract `payload`, `logging.info(json.dumps(payload))` for re-broadcast via PUB. Reply "OK"/"Error".
2. Test with a throwaway zmq REQ script.

### Phase B: Health check script *(ramrod/ramrod_health.py)* — *parallel with A*

3. **Config loader.** Read `ramrod.yaml` — service endpoints (host:port), outpost list, timeouts.
4. **HC client functions.** One per component type, each creates its own ZMQ context/socket with `RCVTIMEO`/`SNDTIMEO`, tears down on exit:
   - `check_camwatcher()` → `{"cmd": "HC"}` to :5566 → parses writer status, heartbeats, disk
   - `check_datapump()` → `HC` to :5556 → parses request count, latency, uptime
   - `check_sentinel()` → `{"task": "STATUS"}` to :5566 → parses engines, queue, health
5. **Report assembly.** Merge HC responses into SYSHEALTH structure. Outpost health derived from CamWatcher response. Each component gets `ok` boolean.
6. **Sentinel delivery.** `{"task": "HEALTH_REPORT", "payload": <report>}` to sentinel:5566 via REQ. On failure: log locally, exit non-zero.
7. **Logging.** `RotatingFileHandler` to `/home/pi/ramrod/logs/ramrod_health.log`.
8. **CLI.** `main()` runs checks sequentially, assembles, delivers, exits. Supports `--dry-run` and `--config`.

### Phase C: Ansible config generation *(depends on B for config schema)*

9. **Ramrod role.** `devops/ansible/roles/ramrod/` — template only: `ramrod.yaml.j2` renders from `sentinelcam_outposts`, `sentinelcam_ports`, service hostnames from `site.yaml`. Defaults for timeouts/intervals.
10. **Config playbook.** `playbooks/configure-ramrod.yaml` targeting `localhost` with `connection: local`. Needs explicit `vars_files` to load `group_vars/all/*.yaml` since localhost isn't in inventory groups.
11. **Sync integration.** Extend `sync-ramrod-from-datasink.sh` to rsync `ramrod/` source from data1 alongside devops/.

### Phase D: Bootstrap *(depends on B, C)*

12. **`bootstrap.sh`**: create dir structure, venv, pip install, copy script, create logs dir, install cron (`*/3 * * * *`), prompt to run config playbook.
13. **`update.sh`**: for subsequent updates — copy updated script from synced source, re-pip if deps changed.

### Phase E: End-to-end verification *(depends on A, D)*

14. Full cycle: ramrod → HC → assemble → sentinel HEALTH_REPORT → PUB → Watchtower receives SYSHEALTH.
15. Degraded modes: component down (partial report), sentinel down (local log, non-zero exit).

---

## Relevant Files

### New
- `ramrod/ramrod_health.py` — main script
- `ramrod/requirements.txt` — pyzmq, pyyaml
- `ramrod/bootstrap.sh`, `ramrod/update.sh` — setup/update helpers
- `ramrod/ramrod.yaml.example` — reference config
- `devops/ansible/roles/ramrod/templates/ramrod.yaml.j2` — config template
- `devops/ansible/roles/ramrod/defaults/main.yaml` — default variables
- `devops/ansible/roles/ramrod/tasks/main.yaml` — config rendering tasks
- `devops/ansible/playbooks/configure-ramrod.yaml` — local config rendering

### Modified
- `sentinel/sentinel/sentinel.py` — HEALTH_REPORT handler in `task_loop` (follows ALERT pattern at ~line 1774)
- `devops/scripts/sync/sync-ramrod-from-datasink.sh` — add `ramrod/` rsync

### Reference (patterns to follow)
- `sentinel/sentinel/sentinel.py` `task_loop` ALERT handler — exact template for HEALTH_REPORT
- `sentinel/sentinel_maintenance.py` — minimal ZMQ REQ client pattern
- `camwatcher/camwatcher/camwatcher.py` `_build_hc_response()` — the HC response ramrod consumes

---

## Verification

1. **HEALTH_REPORT handler**: manual REQ to sentinel:5566, verify payload on PUB
2. **HC clients**: run individually against live components, verify parsing + timeout
3. **Report assembly**: known inputs → verify SYSHEALTH structure
4. **Full cycle**: manual run on buzz → Watchtower receives SYSHEALTH
5. **Degraded**: CamWatcher down → partial report delivered; sentinel down → logged locally
6. **Cron**: 24+ hours sustained, verify cadence and no resource leaks
7. **Config template**: render and verify correct outpost list/ports from inventory
8. **Sync pipeline**: update source on data1, sync to buzz, verify updated script

---

## Further Considerations

1. **Config playbook vars loading.** Since `localhost` isn't in the sentinelcam inventory groups, `configure-ramrod.yaml` needs explicit `vars_files` to bring in `group_vars/all/*.yaml`. This is an unusual pattern — verify it works before committing to this approach. Alternative: add a `ramrod` group to inventory with `localhost` and `ansible_connection: local`.

2. **Sync-ramrod extension.** `ramrod/` is top-level, not under `devops/`. The sync script currently only syncs `devops/`. Either add a second rsync for `ramrod/` from `current_deployment/ramrod/`, or stage it on data1 during code pipeline. The former is simpler and avoids entangling with the component deployment pipeline.

3. **Script architecture.** Use a simple class (`HealthChecker`) with pluggable check methods rather than a flat script. This supports future evolution toward: persistent state between runs, trend detection, direct subscriber checks as failure confirmation, and eventually recovery actions. Don't over-engineer — but don't paint into a corner.
