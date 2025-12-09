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
    └── outpost/           # deployed model files

Config file: /home/<sentinelcam_user>/imagenode.yaml
Service: /etc/systemd/system/imagenode.service
```

## Model Deployment

### MobileNetSSD (Current)

**Status**: Manual deployment required - model files not yet automated

**Current process**:
1. Manually copy to `outpost/mobilenet_ssd/` on target node
2. Configure in host_vars/<hostname>.yaml:
   ```yaml
   detection_objects: mobilenetssd
   accelerator: none  # or ncs2 for Intel Neural Compute Stick
   ```

### Future Model Support

**TODO**: Automate model deployment via Ansible
- Store models in `devops/ansible/files/models/`
- Deploy via copy/synchronize tasks
- Support multiple model types (YOLOv3, custom models)
- Version management and model updates

**Planned structure**:
```
devops/ansible/
├── files/
│   └── models/
│       ├── mobilenet_ssd/
│       │   ├── MobileNetSSD_deploy.prototxt
│       │   └── MobileNetSSD_deploy.caffemodel
│       └── yolov3/
│           ├── yolov3.weights
│           ├── yolov3.cfg
│           └── coco.names
└── roles/imagenode/tasks/
    └── deploy_models.yaml  # Future task file
```

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
ssh <outpost> 'ls -la ~/imagenode/outpost/mobilenet_ssd/'

# Manual deployment (temporary until automated):
scp models/* <outpost>:~/imagenode/outpost/mobilenet_ssd/
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

## See Also

- Main ansible README: `devops/ansible/README.md`
- ImageNode documentation: `imagenode/README.rst`
- CamWatcher role: `roles/camwatcher/README.md`
- Sentinel role: `roles/sentinel/README.md` (AI processing)
