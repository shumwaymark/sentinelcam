# ImageNode Role

Deploys and manages **imagenode** on outpost camera nodes.

## Purpose

ImageNode captures images from cameras and publishes them via ZeroMQ to datasink nodes. Supports PiCamera,
OAK/DepthAI, and USB webcam sources with edge detection via Spyglass (CPU/VPU) and optional DepthAI neural
network pipelines. Person detections trigger sentinel task chains for face recognition and tracking.

## Dependencies

- **sentinelcam_base** — user, venv, directory setup
- **Model registry** on primary datasink — MobileNetSSD and other detection models

## Configuration

Camera configuration lives in `host_vars/<hostname>.yaml` via the `imagenode_config` structure:

```yaml
imagenode_config:
  node_name: east              # Outpost identifier (can differ from hostname)
  cameras:
    O1:                        # Camera type prefix: P* (PiCamera), O* (OAK), W* (Webcam)
      viewname: Front          # View identifier
      resolution: [640, 360]
      framerate: 30
      detector:
        detectobjects: none    # Spyglass detector (mobilenetssd | none)
        accelerator: none      # none | ncs2 | coral
        depthai:               # OAK-only: DepthAI pipeline config
          pipeline: MobileNetSSD
          images: frames
          jpegs: jpegs
          neural_nets:
            Q1: nn
        sentinel_tasks:
          person: GetFaces2    # Task triggered on person detection
```

Datasink mapping is auto-resolved from the `sentinelcam_outposts` registry in `group_vars/all/site.yaml`.

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
    │   └── sentinelcam/        # Common libraries
    └── models/                 # Deployed model files (versioned)
        └── mobilenet_ssd/
            └── YYYY-MM-DD/
```

## See Also

- [Outpost Registry Pattern](../../../docs/configuration/OUTPOST_REGISTRY_PATTERN.md)
- [Model Registry](../../../docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md)
- [Upstream imagenode](../../../../imagenode/README.rst)
