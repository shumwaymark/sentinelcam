# DataPump Role

Deploys and manages **datapump** service on datasink nodes.

## Purpose

**datapump** is the primary data retrieval and storage management engine for the SentinelCam system. The `DataFeed` 
library provides the application programming interface for accessing data and event storage for the SentinelCam 
system. 

## Supported Nodes

- **datasinks**: data1 (any node in `datasinks` group)

## Dependencies

- **sentinelcam_base**: Must run first to provision Python environment and system packages
- **camwatcher**: Sibling service responsible for data capture from outpost nodes and sentinel analysis tasks

## Variables

### Core Configuration (group_vars/all/sentinelcam_ports.yaml)

```yaml
sentinelcam_ports:
  datapump_control: 5556           # TCP control port
```

### Service Configuration (group_vars/datasinks.yaml)

```yaml
datapump_service: datapump
datapump_install_path: /home/ops/camwatcher/datapump
datapump_config_path: /home/ops/datapump.yaml
```

### Optional Overrides

```yaml
datapump_service: datapump         # Service name
datapump_user: "{{ sentinelcam_user }}"  # Service user (pi or ops)
datapump_control_port: 5556        # TCP control port
```

## Deployment

### Initial Setup

```bash
# Deploy full service (includes config, systemd, code)
ansible-playbook playbooks/deploy-datapump.yaml
```

### Code Updates (Most Common)

```bash
# From development workstation
python devops/scripts/sync/deploy.py datapump

# From ramrod control node
ansible-playbook playbooks/deploy-datapump.yaml --tags deploy
```

### Configuration Updates

```bash
# Edit config, then deploy
ansible-playbook playbooks/deploy-datapump.yaml --tags config
```

## File Structure

```
/home/<sentinelcam_user>/
├── camwatcher.yaml        # application configuration
├── datapump.yaml          # application configuration
└── camwatcher/       
    ├── camwatcher/        # Python package
    │   ├── camwatcher.py  # Main application 
    │   └── sentinelcam/   # common libraries
    └── datapump/          # Python package
        ├── datapump.py    # Main application 
        └── sentinelcam/   # common libraries

Config file: /home/<sentinelcam_user>/datapump.yaml
Service: /etc/systemd/system/datapump.service
```

Note: DataPump is deployed under `camwatcher/` directory as a sibling to CamWatcher.

## Configuration

### Example datapump.yaml

```yaml
%YAML 1.0
---
# Settings file datapump.yaml 
control_port: 5556
camwatcher: tcp://127.0.0.1:5566

# Data storage locations  
imagefolder: /home/ops/sentinelcam/images
datafolder:  /home/ops/sentinelcam/camwatcher

# Current FaceList CSV file, defines the population of locked events
facefile: /home/ops/sentinelcam/faces/facebeta6.csv

# Logging configuration
logconfig:
    version: 1
    formatters:
        default:
            format: '%(asctime)s %(levelname)s: %(message)s'
    handlers:
        file:
            class: logging.handlers.RotatingFileHandler
            filename: /home/ops/sentinelcam/logs/datapump.log
            formatter: default
            maxBytes: 524288
            backupCount: 5
            level: WARN
    root:
        handlers: [file]
        level: WARN
```

## Data Management

### Storage Structure

```
  /home/<sentinelcam_user>/
  └── sentinelcam/             # SSD mount point
      ├── camwatcher/          # Collected CSV data from **camwatcher** and **sentinel**
      └── images/              # Collected JPEG data from **camwatcher**
```

## Integration

### With CamWatcher

Receives event notifications from CamWatcher via TCP control port 5556.

### With Sentinel

Provides face images and event data to Sentinel for AI processing via shared filesystem.

## Troubleshooting

### Service Won't Start

```bash
# Check logs
ssh data1 'sudo journalctl -u datapump -n 50'

# Potential issues:
# - Port already in use: Check if another service is using the control port
# - Permission denied: Check directory permissions for data storage
# - Disk space exhaustion: Execute clean-up protocols, possibly add capacity
```

### Events Not Being Stored

```bash
# Check if receiving events
ssh data1 'sudo journalctl -u datapump -f | grep "Event"'

# Verify CamWatcher is sending
ssh data1 'sudo journalctl -u camwatcher -f'

# Check storage usage
ssh data1 'df -h /home/ops/sentinelcam/'
```

## Tags

- `deploy` - Deploy code only (fast)
- `config` - Deploy configuration files
- `service` - Manage systemd service
- `status` - Check service status

## See Also

- Main ansible README: `devops/ansible/README.md`
- DataPump documentation: `camwatcher/datapump/README.rst`
- CamWatcher role: `roles/camwatcher/README.md`
- Sentinel role: `roles/sentinel/README.md`
