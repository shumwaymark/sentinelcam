# Sentinel Role

**Purpose**: Deploys and configures Sentinel, the AI processing engine that performs face detection, face recognition, object classification, and vehicle speed analysis on event data captured by outpost nodes.

## Overview

Sentinel is the inference processing component of SentinelCam, consuming event data from datasinks and applying AI models to extract structured information. It operates as a multi-threaded task engine that processes jobs with different priority levels using configurable hardware accelerators.

### Architecture

```
Outpost Nodes → DataSinks (camwatcher) → Sentinel (AI Processing)
                   ↓                          ↓
              CSV/JPEG Storage          Task Results (fd1, fr1, vsp)
                   ↓                          ↓
              DataPump API  ←─────────────────┘
```

### Key Capabilities

- **Face Detection**: Identifies faces in images using OpenCV DNN or EdgeTPU models
- **Face Recognition**: Matches detected faces against known individuals
- **Object Classification**: Identifies objects (persons, vehicles, etc.) using MobileNet SSD
- **Vehicle Speed Analysis**: Calculates vehicle speeds using perspective-corrected tracking
- **Data Cleanup**: Applies data-aware retention policies to manage storage

## Dependencies

### Required Roles

1. **sentinelcam_base** - Base system provisioning (Python, venv, directories)

### Infrastructure Requirements

- **DataSink nodes** (camwatcher/datapump) - Must be operational to provide event data via DataPump API
- **Model Registry** - Centralized model storage on primary datasink

### Hardware Accelerators

- **Google Coral USB** (recommended) - EdgeTPU runtime via sentinelcam_base
- **CPU** (fallback) - No special hardware required
- **Intel NCS2** (deprecated) - Legacy hardware no longer supported by OpenVINO

## Configuration

### Inventory Setup

Add nodes to the `ai_processing` group in your inventory:

```yaml
# inventory/production.yaml
all:
  children:
    ai_processing:
      hosts:
        sentinel:
          ansible_host: 192.168.10.60
          sentinel_accelerator_type: coral
```

### Required Variables

**Hardware Accelerator** (set in inventory or host_vars):

```yaml
sentinel_accelerator_type: coral  # coral | cpu | ncs2 (deprecated)
```

**Ports** (defined in `group_vars/all/sentinelcam_ports.yaml`):

```yaml
sentinelcam_ports:
  sentinel_control: 5566           # Job submission port
  sentinel_requests: 5566          # Alias for control port
  sentinel_logging: 5565           # Log publishing port
  sentinel_publisher: 5565         # Alias for logging port
  datapump_control: 5556           # DataPump API endpoint
```

### Face Detection Model Selection

When using Coral EdgeTPU, choose your face detection model in `host_vars/<hostname>.yaml`:

```yaml
# Recommended: BlazeFace (fewer false positives, better performance)
sentinel_face_detection_model: blazeface
sentinel_blazeface_variant: full  # full (0-5m) | short (0-2m)
face_detection_confidence:
  blazeface: 0.5

# Alternative: SSD MobileNet (general purpose)
sentinel_face_detection_model: ssd_mobilenet
face_detection_confidence:
  ssd_mobilenet: 0.5
```

**Model Comparison:**

| Model | Performance | False Positives | Best For |
|-------|-------------|-----------------|----------|
| BlazeFace (full) | ~30 FPS | Very low | Surveillance (0-5m) |
| BlazeFace (short) | ~40 FPS | Very low | Mobile/selfie (0-2m) |
| SSD MobileNet | ~14 FPS | Moderate | General purpose |

### Task Engine Configuration

Configure task engines for different priority workloads (optional override in `host_vars`):

```yaml
sentinel_task_engines:
  Alpha:                    # Real-time processing
    classes: [1]            # High-priority tasks
    ring_buffers: default
    accelerator: coral      # Use hardware acceleration
  
  Bravo1:                   # Face recognition
    classes: [2]            # Medium-priority tasks
    ring_buffers: default
    accelerator: cpu        # CPU for embedding generation
  
  Bravo2:                   # Background maintenance
    classes: [2, 3]         # Medium and low priority
    ring_buffers: default
    accelerator: cpu
```

**Task Classes:**
- **Class 1**: Real-time detection (MobileNetSSD, GetFaces)
- **Class 2**: Recognition and analysis (FaceRecon, VehicleSpeed)
- **Class 3**: Maintenance (DailyCleanup)

### Optional Overrides

```yaml
# In host_vars/<hostname>.yaml
debug_mode: true                          # Enable debug logging
model_version_overrides:                  # Test specific model versions
  face_recognition: "2025-12-01"
sentinel_install_coral_packages: true    # Install Coral EdgeTPU packages
```

## Deployment

### Initial Deployment

Full deployment including service, configuration, and code:

```bash
# Deploy to all ai_processing nodes
ansible-playbook playbooks/deploy-sentinel.yaml

# Deploy to specific node
ansible-playbook playbooks/deploy-sentinel.yaml --limit=sentinel
```

### Code Updates (Most Common)

After modifying sentinel Python code:

```bash
# From Windows workstation (recommended)
python devops/scripts/sync/deploy.py sentinel

# From control node
ansible-playbook playbooks/deploy-sentinel.yaml --tags=deploy
```

### Configuration Updates

After editing task configs or sentinel.yaml:

```bash
ansible-playbook playbooks/deploy-sentinel.yaml --tags=config

# Restart service to apply changes
ansible-playbook playbooks/deploy-sentinel.yaml --tags=service --extra-vars="service_state=restarted"
```

### Model Deployment

Models are managed via the centralized model registry. See **Model Management** section below.

## Task Configuration

### Available Tasks

Tasks are defined in `defaults/main.yaml` and configured via templated YAML files in `templates/tasks/`:

| Task | Class | Purpose | Chains To |
|------|-------|---------|-----------|
| **MobileNetSSD_allFrames** | 1 | Object detection on all frames | GetFaces |
| **GetFaces** / **GetFaces2** | 1 | Face detection | FaceRecon |
| **FaceRecon** | 2 | Face recognition and identification | - |
| **VehicleSpeed** | 2 | Vehicle speed calculation | - |
| **FaceSweep** | 2 | Background face candidate collection | - |
| **FaceDataUpdate** | 2 | Face database updates | - |
| **DailyCleanup** | 3 | Data retention and cleanup | - |
| **MeasureRingLatency** | 1 | Performance benchmarking | - |

### Task Configuration Files

Each task has a configuration file in `sentinel/tasks/`:

**GetFaces.yaml** - Face detection parameters:
```yaml
face_detection:
  model: ssd_mobilenet  # or blazeface
  confidence: 0.5
  device: coral
all_frames: false       # false = only frames with persons
camwatcher_update: true # Publish results to camwatcher
trk_type: fd1          # Tracking type identifier
```

**FaceRecon.yaml** - Face recognition settings:
```yaml
face_aligner:
  desiredLeftEye: [0.35, 0.35]
  desiredFaceWidth: 96
  desiredFaceHeight: 96
face_embeddings:
  embedding_size: 128
facemodel: /home/ops/sentinel/models/face_recognition/.../facemodel.pickle
baselines: /home/ops/sentinel/models/face_recognition/.../baselines.hdf5
```

**DailyCleanup.yaml** - Data retention policies:
```yaml
run_deletes: false      # Dry-run mode
max_scan_days: 30       # Days to scan backwards

retention_profiles:
  person_tracking:
    strategy: face_quality
    retention_days: 14
    confidence_threshold: 0.99
    nodes: [lab1, alpha5]
  
  vehicle_tracking:
    strategy: vehicle_interest
    retention_days: 7
    extended_days: 30   # Keep speed violations longer
    nodes: [east]
```

**Key Concepts:**
- **Keep-if-any-valuable**: Events kept if ANY data type has value (quality faces, speed data, etc.)
- **Per-event evaluation**: Each event evaluated based on ALL its data types
- **Node-based profiles**: Simple configuration, data-aware logic

### Modifying Task Configurations

1. **Edit template**: Modify template in `devops/ansible/roles/sentinel/templates/tasks/`
2. **Deploy config**: `ansible-playbook playbooks/deploy-sentinel.yaml --tags=config`
3. **No restart needed**: Sentinel reloads configs dynamically per task

## Model Management

### Model Registry Structure

Models are centrally managed on `primary_datasink` at `/home/ops/sentinelcam/model_registry/`:

```
model_registry/
├── face_detection/
│   └── 2020-03-25/
│       ├── manifest.yaml
│       ├── deploy.prototxt
│       └── res10_300x300_ssd_iter_140000.caffemodel
├── face_detection_edgetpu/
│   └── 2020-03-25/
│       └── ssd_mobilenet_v2_face_quant_postprocess_edgetpu.tflite
├── face_detection_blazeface/
│   └── 2024-03-14/
│       ├── face_detection_full_range_edgetpu.tflite
│       └── face_detection_short_range_edgetpu.tflite
├── face_recognition/
│   └── 2025-02-25/
│       ├── manifest.yaml
│       ├── facemodel.pickle
│       ├── baselines.hdf5
│       ├── facedata.hdf5
│       └── facelist.csv
├── mobilenet_ssd/
│   └── 2020-12-06/
└── openface_torch/
    └── 2020-03-25/
```

### Version Management

Active model versions defined in `inventory/group_vars/all/model_registry.yaml`:

```yaml
sentinelcam_models:
  face_detection:
    current_version: "2020-03-25"
    previous_version: "2020-03-25"
  face_recognition:
    current_version: "2025-02-25"
    previous_version: "2020-03-25"
  # ... other models
```

### Deploying Models

```bash
# Deploy all models to all sentinels
ansible-playbook playbooks/deploy-models.yaml

# Deploy specific model
ansible-playbook playbooks/deploy-models.yaml --tags=face_recognition

# Deploy to specific node
ansible-playbook playbooks/deploy-models.yaml --limit=sentinel

# Deploy with version override
ansible-playbook playbooks/deploy-models.yaml \
  --extra-vars='{"model_version_overrides": {"face_recognition": "2025-12-01"}}'
```

### Rolling Back Models

```bash
# Interactive rollback utility
./devops/scripts/operations/rollback_model.sh

# Quick rollback to previous version
./devops/scripts/operations/rollback_model.sh \
  --model face_recognition --previous

# Rollback to specific version
./devops/scripts/operations/rollback_model.sh \
  --model face_recognition --version 2020-03-25
```

### Model Cleanup

Old model versions are automatically cleaned up weekly via systemd timer on datasink. See `devops/docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md` for details.

## File Structure

```
/home/<sentinelcam_user>/
├── sentinel.yaml              # Main configuration
└── sentinel/
    ├── sentinel/              # Python package
    │   ├── sentinel.py        # Main service
    │   └── sentinelcam/       # Core libraries
    │       ├── taskfactory.py # Task implementations
    │       ├── datafeed.py    # DataPump API client
    │       ├── facedata.py    # Face processing
    │       └── tasklibrary.py # Model wrappers
    ├── sentinel_task.py       # External task injection
    ├── sentinel_ncs2.sh       # NCS2 launcher (legacy)
    ├── sockets/               # IPC sockets
    ├── models/                # Versioned model files
    │   ├── face_detection/
    │   │   └── 2020-03-25/
    │   └── face_recognition/
    │       └── 2025-02-25/
    └── tasks/                 # Task config files
        ├── GetFaces.yaml
        ├── FaceRecon.yaml
        ├── DailyCleanup.yaml
        └── ...
```

**Key Locations:**
- **Service**: `/etc/systemd/system/sentinel.service`
- **Config**: `/home/<user>/sentinel.yaml`
- **Logs**: `journalctl -u sentinel`
- **Virtual env**: `/home/<user>/.virtualenvs/py3cv4/`

## Service Management

### Systemd Commands

```bash
# Start/stop/restart service
sudo systemctl start sentinel
sudo systemctl stop sentinel
sudo systemctl restart sentinel

# Enable/disable at boot
sudo systemctl enable sentinel
sudo systemctl disable sentinel

# View status
sudo systemctl status sentinel

# View logs (live)
sudo journalctl -u sentinel -f

# View recent logs
sudo journalctl -u sentinel -n 100 --no-pager
```

### Via Ansible

```bash
# Check service status on all sentinels
ansible-playbook playbooks/deploy-sentinel.yaml --tags=status

# Restart service
ansible-playbook playbooks/deploy-sentinel.yaml \
  --tags=service --extra-vars="service_state=restarted"

# Stop service
ansible-playbook playbooks/deploy-sentinel.yaml \
  --tags=service --extra-vars="service_state=stopped"
```

## Monitoring and Diagnostics

### Log Messages

Sentinel publishes structured log messages via ZMQ on port 5565:

```bash
# Subscribe to live logs (from any node)
ssh watchtower 'tail -f ~/.watchtower/daemon.log | grep Sentinel'

# View task completion stats
ssh sentinel 'sudo journalctl -u sentinel | grep "EOJ"'

# Example output:
# EOJ (GetFaces, Done), elapsed time: 0:00:02.345, event: 2026-01-08/ABC123
# EOJ (FaceRecon, Done), elapsed time: 0:00:01.234, faces: 5, recognized: 3
```

### Performance Monitoring

```bash
# Monitor task processing times
ssh sentinel 'sudo journalctl -u sentinel -f | grep "elapsed time"'

# Check ring buffer performance
ssh sentinel 'sudo journalctl -u sentinel | grep "Ring"'

# Monitor resource usage
ssh sentinel 'top -b -n 1 | grep -A5 sentinel'

# Check accelerator utilization (Coral)
ssh sentinel 'lsusb -t'  # Verify USB 3.0 connection
```

### Health Checks

```bash
# Verify service is running
ansible ai_processing -m shell -a 'systemctl is-active sentinel'

# Check DataPump connectivity
ssh sentinel 'python3 -c "from sentinelcam.datafeed import DataFeed; \
  df = DataFeed(\"tcp://data1:5556\"); print(df.health_check())"'

# Verify models are loaded
ssh sentinel 'ls -lh ~/sentinel/models/*/$(cat ~/sentinel/sentinel.yaml | grep current_version)'

# Test face detection
ssh sentinel '~/sentinel/test_face_detection.py --image test.jpg'
```

## Troubleshooting

### Service Won't Start

**Check service status and logs:**
```bash
ssh sentinel 'sudo systemctl status sentinel'
ssh sentinel 'sudo journalctl -u sentinel -n 50 --no-pager'
```

**Common Issues:**

| Symptom | Cause | Solution |
|---------|-------|----------|
| ImportError | Missing dependencies | Reinstall venv: `ansible-playbook playbooks/deploy-sentinel.yaml --tags=venv` |
| Permission denied | Wrong user/permissions | Verify `sentinelcam_user` ownership |
| Port already in use | Stale process | `sudo lsof -i :5566` and kill process |
| Models not found | Missing model files | Deploy models: `ansible-playbook playbooks/deploy-models.yaml` |

### Hardware Accelerator Issues

**Coral EdgeTPU not detected:**
```bash
# Check USB connection
ssh sentinel 'lsusb | grep "Global Unichip"'

# Should show: Bus 002 Device 003: ID 1a6e:089a Global Unichip Corp.

# Verify USB 3.0 (SuperSpeed)
ssh sentinel 'lsusb -t | grep -A2 "Global Unichip"'

# Check EdgeTPU runtime
ssh sentinel 'python3 -c "from pycoral.utils import edgetpu; print(edgetpu.list_edge_tpus())"'

# Reinstall Coral packages if needed
ansible-playbook playbooks/deploy-sentinel.yaml --tags=coral_packages
```

**NCS2 not detected (legacy):**
```bash
# Check USB device
ssh sentinel 'lsusb | grep Movidius'

# Verify OpenVINO environment
ssh sentinel 'source ~/openvino/bin/setupvars.sh && echo $INTEL_OPENVINO_DIR'
```

### Model Loading Errors

**Verify model files exist:**
```bash
ssh sentinel 'ls -la ~/sentinel/models/'
ssh sentinel 'tree ~/sentinel/models/face_detection/'
ssh sentinel 'tree ~/sentinel/models/face_recognition/'
```

**Check model versions in config:**
```bash
ssh sentinel 'grep -A5 "current_version" ~/sentinel/sentinel.yaml'
```

**Redeploy models:**
```bash
# Full model redeployment
ansible-playbook playbooks/deploy-models.yaml --limit=sentinel

# Specific model only
ansible-playbook playbooks/deploy-models.yaml --tags=face_recognition --limit=sentinel
```

**Test model loading:**
```bash
# Test Coral model
ssh sentinel 'python3 -c "
from pycoral.utils import edgetpu
from pycoral.adapters import common
interpreter = edgetpu.make_interpreter(\"~/sentinel/models/face_detection_edgetpu/*/ssd_*.tflite\")
interpreter.allocate_tensors()
print(\"Model loaded successfully\")
"'
```

### Performance Issues

**Task processing too slow:**

1. **Check accelerator usage**: Verify Coral is being used, not CPU fallback
   ```bash
   ssh sentinel 'sudo journalctl -u sentinel | grep accelerator'
   ```

2. **Monitor ring buffer status**: Buffer overruns indicate processing bottleneck
   ```bash
   ssh sentinel 'sudo journalctl -u sentinel | grep "ring.*full"'
   ```

3. **Verify USB 3.0 connection**: Coral must be on USB 3.0 for full performance
   ```bash
   ssh sentinel 'lsusb -t | grep -B2 "Global Unichip"'
   # Should show 5000M (SuperSpeed)
   ```

4. **Check CPU load**: High CPU suggests accelerator not working
   ```bash
   ssh sentinel 'top -b -n 1 | head -20'
   ```

**Face recognition taking too long:**

- Face recognition runs on CPU (embeddings generation)
- Consider reducing `retention_days` in DailyCleanup to process less historical data
- Verify face model is loaded correctly

### DataFeed / DataPump Connectivity

**Cannot fetch event data:**
```bash
# Test DataPump connection
ssh sentinel 'python3 -c "
from sentinelcam.datafeed import DataFeed
df = DataFeed(\"tcp://data1:5556\")
print(\"Health check:\", df.health_check())
dates = df.get_date_list()
print(\"Available dates:\", len(dates))
"'

# Check network connectivity
ssh sentinel 'nc -zv data1 5556'

# Verify datapump is running
ssh data1 'sudo systemctl status datapump'
```

### Configuration Errors

**YAML syntax errors:**
```bash
# Validate sentinel.yaml syntax
ssh sentinel 'python3 -c "import yaml; yaml.safe_load(open(\"sentinel.yaml\"))"'

# Validate task configs
ssh sentinel 'for f in ~/sentinel/tasks/*.yaml; do \
  echo "Checking $f"; \
  python3 -c "import yaml; yaml.safe_load(open(\"$f\"))"; \
done'
```

**Model path issues:**
```bash
# Check model paths in config match filesystem
ssh sentinel 'grep -r "models/" ~/sentinel/tasks/*.yaml'
ssh sentinel 'find ~/sentinel/models -type f -name "*.tflite" -o -name "*.pickle"'
```

## Role Tags

Use tags to run specific parts of the role:

| Tag | Description | Use Case |
|-----|-------------|----------|
| `deploy` | Deploy code only (Python files) | After code changes (fastest) |
| `config` | Deploy configuration files | After config/task YAML changes |
| `service` | Manage systemd service | Start/stop/restart service |
| `status` | Check service status | Verify deployment |
| `tasks` | Deploy task configurations only | After task config changes |
| `coral_packages` | Install Coral EdgeTPU packages | Setup new Coral accelerator |

**Examples:**
```bash
# Code update only (fast)
ansible-playbook playbooks/deploy-sentinel.yaml --tags=deploy

# Config update and service restart
ansible-playbook playbooks/deploy-sentinel.yaml --tags=config,service \
  --extra-vars="service_state=restarted"

# Check status on all sentinels
ansible-playbook playbooks/deploy-sentinel.yaml --tags=status
```

## Integration with Other Roles

### Datasink (camwatcher/datapump)

- Sentinel reads event data via DataPump API (port 5556)
- Results published back to camwatcher for watchtower display
- Shared storage required for model registry and face databases

### Watchtower

- Subscribes to Sentinel logs on port 5565
- Displays task completion notifications
- Shows face recognition results in event viewer

### ImageNode (Outposts)

- Outposts send detection tasks to Sentinel via camwatcher
- Sentinel processes and returns enriched tracking data (fd1, fr1, vsp)

## Development and Testing

### Running Tasks Manually

```bash
# Run external task injection
ssh sentinel '~/sentinel/sentinel_task.py -t DailyCleanup -d 2026-01-07'

# Run face sweep for date
ssh sentinel '~/sentinel/sentinel_task.py -t FaceSweep -d 2026-01-07'

# Measure ring latency
ssh sentinel '~/sentinel/sentinel_task.py -t MeasureRingLatency -d 2026-01-07'
```

### Debug Mode

Enable verbose logging in `host_vars/<hostname>.yaml`:

```yaml
debug_mode: true
```

Then redeploy config and restart:
```bash
ansible-playbook playbooks/deploy-sentinel.yaml --tags=config,service \
  --extra-vars="service_state=restarted" --limit=sentinel
```

### Testing Model Changes

1. Add new model version to registry on datasink
2. Override version in `host_vars`:
   ```yaml
   model_version_overrides:
     face_recognition: "2026-01-08"  # Test version
   ```
3. Deploy models: `ansible-playbook playbooks/deploy-models.yaml --limit=sentinel`
4. Restart sentinel to load new models
5. Monitor logs for errors
6. If successful, update `model_registry.yaml` to make permanent

## See Also

- **Sentinel Source**: `sentinel/README.rst`
- **Main Ansible README**: `devops/ansible/README.md`
- **Model Registry Documentation**: `devops/docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md`
- **Task Factory Source**: `sentinel/sentinel/sentinelcam/taskfactory.py`
- **DataPump Role**: `devops/ansible/roles/datapump/README.md`
- **Watchtower Role**: `devops/ansible/roles/watchtower/README.md`
- **Site Configuration**: `devops/docs/configuration/SITE_VARIABLES_REFERENCE.md`
