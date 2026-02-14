# Sentinel Role

Deploys and configures **sentinel**, the AI processing engine for SentinelCam.

## Purpose

Sentinel consumes event data from datasinks and applies AI models for face detection, face recognition,
object classification, and vehicle speed analysis. It operates as a multi-threaded task engine with
priority-based job scheduling and configurable hardware accelerators.

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

### Data Retention (DailyCleanup)

Configured via `templates/tasks/DailyCleanup.yaml.j2` with per-node retention profiles:
- **Keep-if-any-valuable**: events kept if ANY data type has value
- **Per-event evaluation**: quality faces, speed violations, etc.
- **Node-based profiles**: different retention policies per outpost

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

## File Structure (on target)

```
/home/<sentinelcam_user>/
├── sentinel.yaml              # Main configuration
└── sentinel/
    ├── sentinel/              # Python package
    │   ├── sentinel.py
    │   └── sentinelcam/       # Core libraries (taskfactory, datafeed, facedata)
    ├── sentinel_task.py       # External task injection tool
    ├── sockets/               # IPC sockets
    ├── models/                # Versioned model files
    │   ├── face_detection/
    │   ├── face_detection_edgetpu/
    │   ├── face_detection_blazeface/
    │   ├── face_recognition/
    │   └── openface_torch/
    └── tasks/                 # Task config YAML files
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
