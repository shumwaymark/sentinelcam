# ImageNode Role

Deploys and manages **imagenode** on outpost camera nodes.

## Purpose

ImageNode captures images from cameras and publishes them via ZeroMQ to datasink nodes. Supports PiCamera,
OAK/DepthAI, and USB webcam sources with edge detection via Spyglass (CPU/VPU) and optional DepthAI neural
network pipelines. Person and vehicle detections trigger sentinel task chains for recognition and tracking.

Since the §4.10 outpost redesign, **all** node types share one unified, detection-triggered event
lifecycle: a persistent host-side tracker (`hosttracker.py`) + event manager (`eventmanager.py`) own
object identity and event start/end. The picamera path uses Spyglass as a DETECT-only inference engine
with motion detection scheduling the inferences; the OAK path drives the same tracker from the device
neural net on its own threads. The legacy correlation-tracking cascade (dlib/cv2 trackers, CentroidTracker,
SpyGlass scene management) was retired. A node opts into the lifecycle by giving `detector.tracker` a
config dict (see below); omitting it leaves the node publish-only (live scene, no events).

## Dependencies

- **sentinelcam_base** — user, venv, directory setup
- **Model registry** on primary datasink — MobileNetSSD and other detection models

## Configuration

Camera configuration lives in `host_vars/<hostname>.yaml` via the `imagenode_config` structure.

**PiCamera / Coral (host-NN) node** — Spyglass DETECT + host tracker, motion-scheduled:

```yaml
imagenode_config:
  node_name: alpha5            # Outpost identifier (can differ from hostname)
  cameras:
    P1:                        # Camera type prefix: P* (PiCamera), O* (OAK), W* (Webcam)
      viewname: PiCam3         # View identifier
      resolution: (640, 480)
      framerate: 32
      detector:
        detectobjects: mobilenetssd   # Spyglass detector (mobilenetssd | none)
        accelerator: coral            # none | ncs2 | coral
        tracker:                      # a DICT opts into the unified host-tracker lifecycle
          min_confidence_new: 0.50    # see "Host tracker tuning" below
        sentinel_tasks:
          person: GetFaces2           # Task triggered on person detection
          default: MobileNetSSD_allFrames
```

**OAK / DepthAI node** — device NN event plane, host tracker on its own threads:

```yaml
    O1:
      viewname: Front
      resolution: (640, 360)
      framerate: 30
      detector:
        encoder: oak           # oak | cpu (JPEG encoding location)
        detectobjects: none    # device NN does detection; Spyglass not used
        accelerator: none
        tracker:               # host-tracker config — detector level for ALL node types
          min_confidence_new: 0.60
        depthai:               # OAK v3 event-plane config (redesign Phase 3/4)
          lookback_frames: 90  # nn_archive + crop_publish are injected by the template
          oak_pipeline:
            jpeg_quality: 90
            rotate_180: true
            camera_fps: 30
            crop_profiles: { person: {...}, vehicle: {...} }
        sentinel_tasks:
          person: GetFaces2
          default: VehicleSpeed
```

> **§4.10 config note:** `detector.tracker` is a *dict* and lives at the detector level for
> **every** node type — the legacy `tracker: none` string flag and the `depthai.tracker`
> sub-block are retired. An OAK node sets it as a sibling of `depthai:`, not inside it.

### Tuning the detector

The per-node detector knobs — `detector.tracker` (host tracker thresholds),
`crop_profiles` (OAK high-res crop geometry), `motion_params`, and `sentinel_tasks` — each have
a full field reference, defaults, and guidance in **[docs/OUTPOST_CONFIGURATION.md](../../../../docs/OUTPOST_CONFIGURATION.md)**.
The common per-node knob is `detector.tracker.min_confidence_new`, the birth floor tuned from each
node's own confidence distribution after a soak (production: east 0.60 OAK, alpha5/lab1 0.50 host NN).
For the lifecycle concepts these knobs tune, see [docs/TRACKING_ARCHITECTURE.md](../../../../docs/TRACKING_ARCHITECTURE.md).

Datasink mapping is auto-resolved from the `sentinelcam_outposts` registry in `group_vars/all/site.yaml`.

Two further detector settings predate the §4.10 redesign and survived it: `spyglass: (w,h)`,
which **sizes the SpyGlass shared-memory buffer and must match the true pipeline image size**,
and `ROI`, which bounds motion detection in frame percentages. Both are documented in
[OUTPOST_CONFIGURATION.md §5](../../../../docs/OUTPOST_CONFIGURATION.md).

### SpyGlass watchdog

```yaml
detector:
  spyglass_timeout: 30        # seconds; 0 disables
```

An inference accelerator can wedge with the device open and never answer — no exception, no
error return, no kernel event, just a reply that never comes. The outpost presents this as a
heartbeat still reporting a healthy frame rate while its `looks` counter stays frozen: the
main loop, scene publisher and FPS all fine, detection dead.

A deadline is the only available detector and killing the child is the only recovery, since
the blocking call sits in C holding the device file descriptor. On a breach the SpyGlass is
recycled — child killed, IPC wire rebuilt (its socket is stranded mid-transaction), shared
frame buffer kept.

The timer measures **time since anything was readable on the wire**, not time since the
request was sent — a healthy result may sit uncollected during a quiet scene, and timing that
would fire on a perfectly healthy idle camera.

> Set this above a cold start: the child loads its model before it can answer anything. A
> recycle announces itself on its own log line rather than in the heartbeat, whose shape is a
> parsing contract with the **camwatcher**.

### Hardware Accelerator

Set `imagenode_accelerator_type` in host_vars: `coral`, `ncs2`, or `none`. Coral EdgeTPU packages
install automatically when `imagenode_install_coral_packages: true`.

### Models

Models deploy from the centralized registry on the primary datasink. Default: `mobilenet_ssd`.
Override per-node with `outpost_models` list in host_vars.

```bash
ansible-playbook playbooks/deploy-models.yaml --limit=<hostname>
```

## Deployment

```bash
# Full setup (new outpost)
ansible-playbook playbooks/deploy-outpost.yaml --limit <hostname>

# Code update
ansible-playbook playbooks/deploy-outpost.yaml --tags deploy

# Config change
ansible-playbook playbooks/deploy-outpost.yaml --tags config --limit <hostname>
```

## Tags

| Tag | Scope |
|-----|-------|
| `deploy` | Application code sync |
| `config` | Generate and deploy imagenode.yaml |
| `service` | Systemd unit management |
| `coral` | Coral EdgeTPU package provisioning |

## File Structure (on target)

```
/home/<sentinelcam_user>/
├── imagenode.yaml              # Application configuration  
└── imagenode/
    ├── imagenode/              # Python package
    │   ├── imagenode.py
    │   ├── tools/
    │   └── sentinelcam/        # outpost/spyglass and libraries
    └── models/                 # Deployed model files (versioned)
        └── mobilenet_ssd/
            └── YYYY-MM-DD/
```

## See Also

- [Outpost Registry Pattern](../../../docs/configuration/OUTPOST_REGISTRY_PATTERN.md)
- [Model Registry](../../../docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md)
- [Upstream imagenode](../../../../imagenode/README.rst)
