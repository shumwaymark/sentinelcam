# ImageNode Role

Deploys and manages **imagenode** service on outpost camera nodes.

## Purpose

ImageNode captures images from cameras and publishes them via ZeroMQ to ImageHub datasinks. Supports edge detection, hardware acceleration, and multi-modal inference.

## Architecture

- **Node Name**: Internal identifier for the outpost (e.g., "east", "alpha5") - can differ from hostname
- **View Name**: Each camera on the node is assigned a viewname for identifcation
- **Camera Types**: Identified by prefix - P* (PiCamera), O* (OAK/DepthAI), W* (Webcam)
- **Datasink Mapping**: Automatically resolved from `sentinelcam_outposts` registry
- **Edge Processing**: Spyglass pipeline (CPU/VPU) + optional DepthAI pipeline (OAK devices)

## Camera Configuration in host_vars

Define cameras in `host_vars/<hostname>.yaml`:

### Example 1: PiCamera with MobileNetSSD

```yaml
# host_vars/alpha5.yaml
imagenode_config:
  node_name: alpha5              # Internal tag (appears in logs)
  heartbeat: 10                  # Heartbeat interval in seconds
  cameras:
    P1:                          # PiCamera type
      viewname: PiCam3           # View identifier
      resolution: [640, 480]
      framerate: 32
      vflip: false
      detector:
        detectobjects: mobilenetssd    # Spyglass detector
        accelerator: none              # none | ncs2 | coral
        sentinel_tasks:
          person: GetFaces2            # Task to trigger on person detection
          default: MobileNetSSD_allFrames  # Catch-all task (optional)
```

### Example 2: OAK-1 with DepthAI Pipeline

```yaml
# host_vars/east.yaml
imagenode_config:
  node_name: east
  heartbeat: 15
  cameras:
    O1:                          # OAK camera type
      viewname: Front
      resolution: [640, 360]
      framerate: 30
      detector:
        encoder: oak             # oak | cpu (JPEG encoding location)
        detectobjects: none      # Spyglass not used with DepthAI
        accelerator: none
        depthai:                 # DepthAI-specific config
          pipeline: MobileNetSSD
          images: frames         # Image queue name
          jpegs: jpegs          # JPEG queue name
          neural_nets:
            Q1: nn              # Neural network output queue
        sentinel_tasks:
          person: GetFaces2
```

### Example 3: Multi-Camera Setup

```yaml
# host_vars/multinode.yaml
imagenode_config:
  node_name: multinode
  heartbeat: 10
  cameras:
    P1:
      viewname: Indoor
      resolution: [1920, 1080]
      framerate: 15
      detector:
        detectobjects: mobilenetssd
        accelerator: coral       # Using Coral TPU
        sentinel_tasks:
          person: GetFaces2
    O1:
      viewname: Outdoor
      resolution: [640, 360]
      framerate: 30
      detector:
        encoder: oak
        detectobjects: none
        depthai:
          pipeline: MobileNetSSD
          images: frames
          jpegs: jpegs
          neural_nets:
            Q1: nn
        sentinel_tasks:
          person: GetFaces2
          vehicle: VehicleDetect
```

## Deployment

### Initial Setup (New Outpost)

```bash
# 1. Add node to inventory/production.yaml
# 2. Create host_vars/<hostname>.yaml with camera config
# 3. Deploy
ansible-playbook playbooks/deploy-outpost.yaml --limit <hostname>
```

### Code Updates (Most Common)

```bash
# From development workstation
python devops/scripts/sync/deploy.py imagenode

# From ramrod control node
ansible-playbook playbooks/deploy-outpost.yaml --tags deploy
```

### Configuration Updates

```bash
# Edit host_vars/<hostname>.yaml
# Deploy config only
ansible-playbook playbooks/deploy-outpost.yaml --tags config --limit <hostname>
```

## File Structure

```
/home/<sentinelcam_user>/
├── imagenode.yaml         # application configuration
└── imagenode/                
    ├── imagenode/         # Python package
    │   ├── __init__.py
    │   ├── imagenode.py   # Main application
    │   ├── tools/         # imagenode libraries
    │   └── sentinelcam/   # outpost, spyglass, and sentinelcam common libraries
    └── models/            # deployed model files (versioned)
        └── mobilenet_ssd/
            └── 2020-12-06/

Config file: /home/<sentinelcam_user>/imagenode.yaml
Service: /etc/systemd/system/imagenode.service
```

## Model Deployment

### Automated Model Registry

**Status**: Fully automated via centralized model registry

Models are deployed from the model registry on `primary_datasink` to outpost nodes:

**Registry Structure**:
```
/home/ops/sentinelcam/model_registry/
├── mobilenet_ssd/
│   └── 2020-12-06/          # Version timestamp
│       ├── MobileNetSSD_deploy.prototxt
│       ├── MobileNetSSD_deploy.caffemodel
│       └── manifest.yaml
├── face_detection/
│   └── 2020-03-25/
└── vehicle_detection/
    └── 2024-11-15/
```

**Deployment**:
```bash
# Deploy all models to all outposts
ansible-playbook playbooks/deploy-models.yaml

# Deploy specific model
ansible-playbook playbooks/deploy-models.yaml --tags=mobilenet_ssd

# Deploy to specific host
ansible-playbook playbooks/deploy-models.yaml --limit=east
```

**Filtering Models per Node**:

By default, only `mobilenet_ssd` is deployed to outposts. Override in `host_vars/<hostname>.yaml`:

```yaml
# Deploy additional models to specific node
outpost_models:
  - mobilenet_ssd
  - face_detection
```

**Version Management**:

Model versions are defined in `inventory/group_vars/all/model_registry.yaml`:
```yaml
sentinelcam_models:
  mobilenet_ssd:
    current_version: "2020-12-06"
    previous_version: "2020-03-25"
```

See `devops/docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md` for complete documentation.

## Hardware Acceleration

### Intel Neural Compute Stick 2 (NCS2)

Requires OpenVINO toolkit:
```yaml
accelerator: ncs2
```

ImageNode will use `imagenode_ncs2.sh` launcher script which sources OpenVINO environment before starting Python.

### Google Coral Edge TPU

**TODO**: Not yet implemented
```yaml
accelerator: coral
```

## Troubleshooting

### Camera Not Detected

```bash
# PiCamera
ssh <outpost> 'vcgencmd get_camera'
# Should show: supported=1 detected=1

# DepthAI
ssh <outpost> 'python3 -c "import depthai; print(depthai.Device.getAllAvailableDevices())"'
```

### Service Fails to Start

```bash
# Check logs
ssh <outpost> 'sudo journalctl -u imagenode -n 50'

# Common issues:
# - Camera permissions: Add user to video group
# - Missing models: Check outpost/mobilenet_ssd/ directory
# - Hub unreachable: Verify network connectivity to datasink
```

### Model Files Missing

```bash
# Check if models exist
ssh <outpost> 'ls -la ~/imagenode/models/mobilenet_ssd/'

# Verify model version deployed
ssh <outpost> 'ls -la ~/imagenode/models/mobilenet_ssd/2020-12-06/'

# Redeploy models if missing
ansible-playbook playbooks/deploy-models.yaml --limit=<outpost>
```

### Images Not Reaching Hub

```bash
# Check ImageHub is listening
ssh data1 'sudo netstat -tlnp | grep 5555'

# Check ImageNode can connect
ssh <outpost> 'nc -zv data1 5555'

# View ImageNode logs
ssh <outpost> 'sudo journalctl -u imagenode -f'
```

## Tags

- `deploy` - Deploy code only (fast)
- `config` - Deploy configuration files
- `service` - Manage systemd service
- `status` - Check service status
- `health` - Run health checks

## References

- [Baseline Documentation](../../../../imagenode/README.rst)
- [Modifications for SentinelCam](../../../../docs/YingYangRanch_Changes.rst)
