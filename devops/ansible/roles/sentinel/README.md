# Sentinel Role

Deploys and manages Sentinel AI processing service on dedicated AI nodes.

## Purpose

Sentinel performs AI inference tasks on captured images including face detection, face recognition, and object classification. Uses hardware acceleration (Intel NCS2, Google Coral) for efficient processing.

## Supported Nodes

- **AI Processing**: sentinel (any node in `ai_processing` group)

## Dependencies

- **sentinelcam_base**: Must run first to provision Python environment and system packages
- **DataPump**: Provides face images and event data via shared storage
- **OpenVINO** (for NCS2) or **EdgeTPU runtime** (for Coral)

## Variables

### Core Configuration (group_vars/all/sentinelcam_ports.yaml)

```yaml
sentinelcam_ports:
  sentinel_control: 5566           # TCP control port
  sentinel_logging: 5565           # Log publishing port
```

### Required Configuration

```yaml
sentinel_accelerator_type: ncs2    # cpu | ncs2 | coral
sentinel_user: "{{ ansible_user }}"  # Service user (pi or ops)
```

### Optional Overrides

```yaml
sentinel_service: sentinel         # Service name
sentinel_models_path: /home/ops/sentinel/models
debug_mode: false                  # Enable debug logging
```

## Deployment

### Initial Setup

```bash
# Deploy full service (includes config, systemd, code)
ansible-playbook playbooks/deploy_sentinel.yaml
```

### Code Updates (Most Common)

```bash
# From Windows workstation
python devops/scripts/sync/deploy.py sentinel

# From buzz
ansible-playbook playbooks/deploy-sentinel.yaml --tags deploy
```

### Configuration Updates

```bash
# Edit config, then deploy
ansible-playbook playbooks/deploy-sentinel.yaml --tags config
```

## File Structure

```
/home/<sentinelcam_user>/ 
├── sentinel.yaml          # application configuration
└── sentinel/                
    ├── sentinel/          # Python package
    │   ├── sentinel.py    # Main application
    │   └── sentinelcam/   # common libraries
    ├── sentinel_task.py   # external task injection utility
    ├── sockets/           # posix sockets for IPC
    ├── models/            # model data
    └── tasks/             # task configuration YAML files

Config file: /home/<sentinelcam_user>/sentinel.yaml
Service: /etc/systemd/system/sentinel.service
Launcher: /home/<sentinelcam_user>/sentinel/sentinel_ncs2.sh
```

## Model Deployment

### Automated Model Registry

**Status**: Fully automated via centralized model registry

Models are deployed from the model registry on `primary_datasink` to sentinel nodes:

**Registry Structure**:
```
/home/ops/sentinelcam/model_registry/
├── mobilenet_ssd/
│   └── 2020-12-06/
├── face_detection/
│   └── 2020-03-25/          # OpenVINO IR format
│       ├── opencv_dnn_face/
│       ├── manifest.yaml
│       └── ...
├── face_recognition/
│   └── 2025-02-25/          # gamma3 trained model
│       ├── face_pics/
│       ├── faces.db
│       ├── labels.pickle
│       └── manifest.yaml
├── haarcascades/
│   └── 2020-03-25/
└── openface_torch/
    └── 2020-03-25/
```

**Deployment**:
```bash
# Deploy all models to all sentinels
ansible-playbook playbooks/deploy-models.yaml

# Deploy specific model
ansible-playbook playbooks/deploy-models.yaml --tags=face_recognition

# Deploy to specific sentinel
ansible-playbook playbooks/deploy-models.yaml --limit=sentinel
```

**Version Management**:

Model versions are defined in `inventory/group_vars/all/model_registry.yaml`:
```yaml
sentinelcam_models:
  face_detection:
    current_version: "2020-03-25"
    previous_version: "2020-03-25"
  face_recognition:
    current_version: "2025-02-25"
    previous_version: "2020-03-25"
```

**Rollback**:
```bash
# Interactive rollback utility
./devops/scripts/rollback_model.sh

# Quick rollback to previous version
./devops/scripts/rollback_model.sh --model face_recognition --previous
```

See `devops/docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md` for complete documentation.

## Hardware Acceleration

### Intel Neural Compute Stick 2 (NCS2)

Requires OpenVINO toolkit installed via sentinelcam_base role.

```yaml
sentinel_accelerator_type: ncs2
```

Sentinel uses launcher script that sources OpenVINO environment:
```bash
/home/ops/sentinel/sentinel_ncs2.sh
```

### Google Coral Edge TPU

**TODO**: Not yet implemented

```yaml
sentinel_accelerator_type: coral
```

Requires Edge TPU runtime and TensorFlow Lite models.

### CPU Fallback

```yaml
sentinel_accelerator_type: cpu
```

Uses CPU-only inference. Slower but requires no special hardware.

## Task Engines

Sentinel runs multiple task engines processing different priority classes:

### Task Configuration Files

Task configurations are deployed as Jinja2 templates with versioned model paths:

Located in `sentinel/tasks/`:
- `MobileNetSSD_allFrames.yaml` - Object detection on all frames
- `GetFaces.yaml` - Face detection and extraction
- `FaceRecon.yaml` - Face recognition and identification
- `FaceSweep.yaml` - Background face processing
- `FaceDataUpdate.yaml` - Face database management
- `DailyCleanup.yaml` - Maintenance tasks

**Template Deployment**:
```bash
# Deploy task configs with updated model versions
ansible-playbook playbooks/deploy-sentinel.yaml --tags=config
```

Model paths are automatically injected from `model_registry.yaml` during deployment.

### Task Engines

```yaml
Alpha:     # High priority - real-time detection
  classes: [1]
  accelerator: ncs2
  
Bravo1:    # Medium priority - face recognition
  classes: [2]
  accelerator: cpu
  
Bravo2:    # Low priority - maintenance
  classes: [2, 3]
  accelerator: cpu
```

## Configuration

### Example sentinel.yaml

```yaml
%YAML 1.0
---
# Settings file sentinel.yaml 
control_port: 5566
logging_port: 5565
default_pump: tcp://data1:5556
socket_dir: /home/ops/sentinel/sockets

# For ring buffer allocations, any potential image size must
# be known in advance. Each of the task_engines need a set of 
# ring buffers. Parameters are ((width, height), buffer_length)
ring_buffer_models:
    default:
        vga: ((640, 480), 5)
        sd: ((640, 360), 5)

# Probably no more than three of these on a Raspberry Pi 4B?
# Ideally, one would have a dedicated co-processor for real-time 
# response, and others for background maintenance tasks requiring
# only low to moderate CPU resources. Scheduling batch workloads 
# into idle time periods is helpful. 
task_engines:
    Alpha: 
        classes: [1]
        ring_buffers: default
        accelerator: coral      # [cpu, ncs2, coral]
    Bravo1: 
        classes: [2]
        ring_buffers: default
        accelerator: cpu
    Bravo2: 
        classes: [2,3]
        ring_buffers: default
        accelerator: cpu
    
# The list of currently available tasks. These can be configured
# by job class to have an affinity with a particular task engine.
# Each task must have a matching class definition in the TaskFactory
# or include an alias reference to the underlying task.
task_list:
    MobileNetSSD_allFrames:
        config: /home/ops/sentinel/tasks/MobileNetSSD_allFrames.yaml
        chain: GetFaces  # optional, next task when this completes
        class: 1
    GetFaces:
        config: /home/ops/sentinel/tasks/GetFaces.yaml
        chain: FaceRecon 
        class: 1
        trk_type: obj    # desired tracking feed, default = trk
        ringctrl: full   # [full, trk]  default = full
    FaceRecon:
        config: /home/ops/sentinel/tasks/FaceRecon.yaml
        class: 2
        trk_type: fd1    # desired tracking feed, default = trk
        ringctrl: trk    # [full, trk]  default = full
    FaceSweep:
        config: /home/ops/sentinel/tasks/FaceSweep.yaml
        class: 2
    FaceDataUpdate:
        config: /home/ops/sentinel/tasks/FaceDataUpdate.yaml
        class: 2
    GetFaces2:
        alias: GetFaces
        config: /home/ops/sentinel/tasks/GetFaces.yaml
        chain: FaceRecon 
        class: 1
    DailyCleanup:
        config: /home/ops/sentinel/tasks/DailyCleanup.yaml
        class: 3
    MeasureRingLatency:
        config: /home/ops/sentinel/tasks/MeasureRingLatency.yaml
        class: 1

# Configure logging publication over 0MQ
logconfig:
    version: 1
    handlers:
        zmq:
            class: zmq.log.handlers.PUBHandler
            interface_or_socket: tcp://*:5565
            root_topic: Sentinel
            level: INFO
    root:
        handlers: [zmq]
        level: INFO

```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
ssh sentinel 'sudo journalctl -u sentinel -n 50'

# Common issues:
# - OpenVINO not found: Verify openvino environment is set
# - Models missing: Check sentinel/models/ directory
# - NCS2 not detected: Check USB connection and permissions
# - Face database locked: Verify no other process accessing faces.db
```

### NCS2 Not Detected

```bash
# Check USB devices
ssh sentinel 'lsusb | grep Movidius'

# Check OpenVINO environment
ssh sentinel 'source ~/openvino/bin/setupvars.sh && echo $INTEL_OPENVINO_DIR'

# Test NCS2
ssh sentinel '~/openvino/deployment_tools/inference_engine/samples/hello_query_device'
```

### Model Loading Errors

```bash
# Verify model files exist
ssh sentinel 'ls -la ~/sentinel/models/'

# Check versioned model directories
ssh sentinel 'ls -la ~/sentinel/models/face_detection/2020-03-25/'
ssh sentinel 'ls -la ~/sentinel/models/face_recognition/2025-02-25/'

# Check model format (IR for NCS2)
ssh sentinel 'file ~/sentinel/models/face_detection/*/opencv_dnn_face/*.xml'

# Redeploy models if missing or corrupted
ansible-playbook playbooks/deploy-models.yaml --limit=sentinel

# Test model loading
ssh sentinel 'python3 -c "from openvino.inference_engine import IECore; ie = IECore(); print(ie.available_devices)"'
```

### Performance Issues

```bash
# Monitor task engine performance
ssh sentinel 'sudo journalctl -u sentinel -f | grep "Task.*ms"'

# Check ring buffer status
ssh sentinel 'sudo journalctl -u sentinel | grep "Ring buffer"'

# Monitor CPU/memory
ssh sentinel 'top -b -n 1 | grep sentinel'
```

## Tags

- `deploy` - Deploy code only (fast)
- `config` - Deploy configuration files
- `service` - Manage systemd service
- `status` - Check service status
- `tasks` - Deploy task configurations

## See Also

- Main ansible README: `devops/ansible/README.md`
- Sentinel documentation: `sentinel/README.rst`
- DataPump role: `roles/datapump/README.md`
- ImageNode role: `roles/imagenode/README.md`
