# Sentinel Role

Deploys and configures **sentinel**, the AI processing engine for SentinelCam.

## Purpose

Sentinel consumes event data from datasinks and applies AI models for face detection, face recognition,
object classification, and vehicle speed analysis. It operates as a multi-processing task engine with
priority-based job scheduling, configurable hardware accelerators, engine health monitoring, and
state persistence across restarts.

```
Outposts → CamWatcher (datasink) → Sentinel → Task Results → DataPump/Watchtower
```

## Dependencies

- **sentinelcam_base** — user, venv, directory setup
- **DataSink services** (camwatcher/datapump) — event data source via DataFeed API
- **Model registry** on primary datasink — face detection, recognition, and classification models

## Configuration

### Hardware Accelerator

Set in inventory or host_vars:

```yaml
sentinel_accelerator_type: coral  # coral | cpu | ncs2 (deprecated)
```

### Face Detection Model

Choose model in `host_vars/<hostname>.yaml`:

```yaml
sentinel_face_detection_model: blazeface   # blazeface | ssd_mobilenet
sentinel_blazeface_variant: full           # full (0-5m) | short (0-2m)
```

| Model | Speed | False Positives | Range |
|-------|-------|-----------------|-------|
| BlazeFace full | ~30 FPS | Very low | 0-5m |
| BlazeFace short | ~40 FPS | Very low | 0-2m |
| SSD MobileNet | ~14 FPS | Moderate | General |

### Task Engines

Task engines are defined in `defaults/main.yaml`. Override in host_vars if needed:

```yaml
sentinel_task_engines:
  Alpha:              # Real-time detection (class 1)
    classes: [1]
    accelerator: coral
  Bravo1:             # Recognition/analysis (class 2)
    classes: [2]
    accelerator: cpu
  Bravo2:             # Background + maintenance (class 2-3)
    classes: [2, 3]
    accelerator: cpu
```

### Available Tasks

| Task | Class | Purpose | Chains To |
|------|-------|---------|-----------|
| MobileNetSSD_allFrames | 1 | Object detection on all frames | GetFaces |
| GetFaces / GetFaces2 | 1 | Face detection | FaceRecon |
| FaceRecon | 2 | Face recognition and identification | — |
| VehicleSpeed | 2 | Vehicle speed calculation | — |
| FaceSweep | 2 | Background face candidate collection | — |
| FaceDataUpdate | 2 | Face database/embedding updates | — |
| DailyCleanup | 3 | Data retention and cleanup | — |

Task configuration templates live in `templates/tasks/`. Sentinel reloads task configs dynamically —
no service restart needed after config-only changes.

### Engine Health Monitoring

The sentinel tracks per-engine health and restarts an engine automatically when it
degrades. Particularly relevant for Google Coral accelerators, which fail by slowing
down or wedging rather than by raising.

```yaml
sentinel_health:
  startup_allowance: 3.0       # sec of fixed per-job cost discounted before scoring
  low_rate_threshold: 8.0      # corrected frames/sec at or below this = degraded
  min_rate_frames: 20          # jobs smaller than this are not scored at all
  strike_limit: 3              # consecutive degraded jobs before restart
  fail_strike_limit: 5         # consecutive outright failures before restart
  job_runtime_limit: 900       # wall-clock ceiling on a RUNNING job; 0 disables
  restart_cooldown: 300        # seconds between automatic restarts per engine
  max_auto_restarts: 10        # restarts allowed inside the window below
  restart_window_hours: 6      # trailing window the budget is measured over
```

**Three disjoint detectors.** Each owns a distinct failure, and the separation is
deliberate:

| Detector | Owns | Keyed off |
|----------|------|-----------|
| `job_runtime_limit` | "never finished" | a running job's wall clock |
| `strike_limit` | "finished slowly" | completion, scored on throughput |
| `fail_strike_limit` | "failed outright" | completion |

A wedged accelerator produces a job that never ends — its blocking call sits in C
where no Python signal handler runs and no stop flag is read. Both strike counters key
off a *completion* event, so neither can observe that state; only the deadline can. A
per-task `runtime_limit` overrides the global ceiling.

**Degradation is scored on throughput, not on the job's wall clock.** The wall-clock
rate is dominated by model load and therefore tracks job *size* as much as device
health — on a Coral engine, `elapsed ≈ 2.83s fixed + images / 26.8 fps`, so a 30-image
job reads about 8 fps on hardware running flat out. Scoring it that way restarted a
healthy engine roughly daily. With `startup_allowance` discounted, every job size
converges on ~27 fps, so `low_rate_threshold: 8.0` finally means one thing:
*under 30% of normal*. Jobs below `min_rate_frames` are not scored at all.

**The restart budget is a trailing window, not a lifetime cap.** The fault it guards —
a long batch job overheating the accelerator into thermal throttling — is recoverable
and recurring, so restarts legitimately repeat over a long-lived process. A budget keyed
to uptime disarms itself: nine days of ordinary operation exhausted a limit of ten,
after which the engine had no self-healing left at all. Measured over
`restart_window_hours`, thermal recovery once or twice an hour never approaches the
limit, while a genuine restart loop still spends it within the hour and stops.
Set `restart_window_hours: 0` to restore the old per-uptime behavior.

### State Maintenance

A daily maintenance timer triggers summarization, state checkpointing, and history trimming.
The state file is local to the sentinel node for crash recovery at startup.

```yaml
sentinel_maintenance:
  retention_hours: 48          # completed records older than this are trimmed
  state_file: "{{ sentinel_install_path }}/state.json"  # local to sentinel
  checkpoint_interval: 50      # job completions between incremental checkpoints
```

Health summary records are published via ZMQ and captured by the SentinelAgent on the
data sink into the existing camwatcher date directory structure.

### Graceful Shutdown

The sentinel supports orderly shutdown that preserves queue state and job history across
restarts. SIGTERM (from `systemctl stop` or Ansible deploys) triggers the same drain
sequence as the explicit `SHUTDOWN` control command.

```yaml
sentinel_shutdown:
  drain_timeout: 60            # seconds to wait for running tasks before force-exit
```

During the drain, running tasks continue to completion while no new jobs are assigned.
After all engines go idle (or timeout), state is serialized to disk. Jobs that don't
finish within the timeout are reset to Queued and re-execute on the next startup.

The systemd unit file uses `TimeoutStopSec=90` to allow the drain to complete before
systemd sends SIGKILL.

### Data Retention (DailyCleanup)

`templates/tasks/DailyCleanup.yaml.j2` renders the deployed policy from inventory
variables on every run — **never hand-edit the task file on the node.** To tune
retention, override the relevant scalar in `group_vars` or
`host_vars/<sentinel-host>.yaml` and re-run `deploy-sentinel.yaml`.

Logic: an event is **kept if ANY data type it carries still has value**, and deleted
only when all of it is past retention. Profiles are per-node — quality faces on person
nodes, speed violations on vehicle nodes.

```yaml
sentinel_cleanup_run_deletes: true          # false = report only, take no action
sentinel_cleanup_max_scan_days: 14          # days scanned backwards from run date

# person_tracking profile (face_quality)
sentinel_cleanup_person_retention_days: 2
sentinel_cleanup_person_min_face_ratio: 0.15
sentinel_cleanup_person_confidence_threshold: 0.975

# vehicle_tracking profile (vehicle_interest)
sentinel_cleanup_vehicle_retention_days: 1  # keeps ALL events, incl. non-speed transits
sentinel_cleanup_vehicle_speed_cutoff: 45.0 # beyond extended_days, only these survive
sentinel_cleanup_vehicle_extended_days: 2

sentinel_cleanup_default_retention_days: 7  # minimal_retention fallback profile
```

#### Scene retention — a second, independent clock

Scene frames are ~99% of the data store and the first thing to lose value. Measured on
the primary data sink: 360 GB of frames against 2.0 GB of crops and 604 MB of CSVs — the
entire analytical corpus is 0.7% of the store.

Scene frames therefore expire **independently of the retention verdict above**. An event
past its scene window survives in full — still indexed, still carrying its tracking data
and high-resolution crops — it simply can no longer be replayed as video. Events locked
as model ground truth are exempt and keep everything.

```yaml
sentinel_cleanup_person_scene_retention_days: ~   # null = never expire
sentinel_cleanup_vehicle_scene_retention_days: ~
sentinel_cleanup_default_scene_retention_days: ~
sentinel_cleanup_scene_expiry_band: 3       # days past the window still eligible
```

Null means never, so **no node changes behavior until its profile opts in**.

> **`max_scan_days` must reach PAST the scene window or expiry never fires at all.**
> The default 14 against a 30-day scene policy expires nothing, ever. Note also that
> widening the scan deepens the reach of the *retention* pass too — the first run after
> such a change sweeps previously unreachable backlog, which is intended but is a
> one-time larger-than-usual deletion. Read one report-only run first if that matters.

`scene_expiry_band` bounds the nightly work to events actually crossing the threshold;
without it every run re-issues deletes for every already-expired event in the scan
window. Widen it for a one-time catch-up sweep.

## Deployment

```bash
# Full setup (new sentinel node)
ansible-playbook playbooks/deploy-sentinel.yaml

# Code update
ansible-playbook playbooks/deploy-sentinel.yaml --tags deploy

# Config change (task configs reload without restart)
ansible-playbook playbooks/deploy-sentinel.yaml --tags config

# Model deployment (from centralized registry)
ansible-playbook playbooks/deploy-models.yaml --limit sentinel
```

## Tags

| Tag | Scope |
|-----|-------|
| `deploy` | Application code sync |
| `config` | Generate sentinel.yaml and task config files |
| `service` | Systemd service management |
| `tasks` | Task configuration files only |
| `coral` | Coral EdgeTPU package provisioning |
| `status` | Check service state |
| `maintenance` | Maintenance timer and trigger script |

## File Structure (on target)

```
/home/<sentinelcam_user>/
├── sentinel.yaml               # Main configuration
└── sentinel/
    ├── sentinel/               # Python package
    │   ├── sentinel.py
    │   └── sentinelcam/        # Core libraries (taskfactory, datafeed, facedata)
    ├── sentinel_task.py        # External task injection tool
    ├── sentinel_maintenance.py # Maintenance trigger (systemd timer)
    ├── sentinel_shutdown.py    # Graceful shutdown trigger script
    ├── sentinel_adhoc.py       # Dynamically spawn a camwatcher agent for a sentinel
    ├── state.json              # State checkpoint (transient, deleted on load)
    ├── sockets/                # IPC sockets
    ├── models/                 # Versioned model files
    │   ├── face_detection/
    │   ├── face_detection_edgetpu/
    │   ├── face_detection_blazeface/
    │   ├── face_recognition/
    │   └── openface_torch/
    └── tasks/                  # Task config YAML files
```

### Manual Task Injection

```bash
ssh sentinel '~/sentinel/sentinel_task.py -t DailyCleanup -d 2026-01-07'
ssh sentinel '~/sentinel/sentinel_task.py -t FaceSweep -d 2026-01-07'
```

## See Also

- [Model Registry](../../../docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md)
- [Facial Recognition Pipeline](../../../../docs/FACIAL_RECON_LEARNING.md)
- [Gamma4 Workflow](../../../../docs/GAMMA4_WORKFLOW.md)
- [DataPump role](../datapump/README.md) — DataFeed API provider
- [Watchtower role](../watchtower/README.md) — result display subscriber
