# CamWatcher Role

Deploys and manages CamWatcher service on datasink nodes.

## Purpose

CamWatcher monitors imagenode camera streams, coordinates event-driven image capture, and manages event detection and notification. Works in conjunction with ImageHub and DataPump to process camera events.

## Supported Nodes

- **DataSinks**: data1 (any node in `datasinks` group)

## Dependencies

- **sentinelcam_base**: Must run first to provision Python environment and system packages

## Variables

### Core Configuration

CamWatcher configuration is **auto-generated** from the `sentinelcam_outposts` registry in `group_vars/all/site.yaml`. The template automatically subscribes to outposts where `datasink` matches this node's `inventory_hostname`.

**Example**: If data1 is the datasink, camwatcher.yaml will include all outposts with `datasink: data1`.

See `devops/ansible/OUTPOST_REGISTRY_PATTERN.md` for complete documentation.

### Port Configuration (group_vars/all/sentinelcam_ports.yaml)

```yaml
sentinelcam_ports:
  camwatcher_control: 5566         # TCP control port for sentinel/watchtower
  imagenode_publisher: 5567        # ZeroMQ image publisher (outposts)
  imagenode_logging: 5565          # ZeroMQ log publisher (outposts)
```

### Service Configuration (group_vars/datasinks.yaml)

```yaml
camwatcher_service: camwatcher
camwatcher_install_path: /home/ops/camwatcher/camwatcher
camwatcher_config_path: /home/ops/camwatcher.yaml
```

### Optional Overrides

```yaml
camwatcher_service: camwatcher     # Service name
camwatcher_user: "{{ sentinelcam_user }}"  # Service user (pi or ops)
debug_mode: false                  # Enable debug logging
```

## Deployment

### Initial Setup

```bash
# Deploy full service (includes config, systemd, code)
ansible-playbook playbooks/deploy-camwatcher.yaml
```

### Code Updates (Most Common)

```bash
# From Windows workstation
python devops/scripts/sync/deploy.py camwatcher

# From buzz
ansible-playbook playbooks/deploy-camwatcher.yaml --tags deploy
```

### Configuration Updates

```bash
# Edit config, then deploy
ansible-playbook playbooks/deploy-camwatcher.yaml --tags config
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

Config file: /home/<sentinelcam_user>/camwatcher/camwatcher.yaml
Service: /etc/systemd/system/camwatcher.service
```

Note: CamWatcher is deployed under `camwatcher/` directory as a sibling to DataPump.

## Configuration

### Example camwatcher.yaml

```yaml
%YAML 1.0
---
# Settings file camwatcher.yaml 
control_port: 5566
datapump_port: 5556

# The list of known outpost nodes. Subscriptions to these are established 
# at start-up. New nodes may also be added dynamically whenever introduced
# through a camera handoff over the control_port. Dynamically added nodes 
# are cleared by a restart. 
outpost_nodes:
  #  Each outpost should match the node["name"] setting for the imagenode 
  east:
    view: Front
    logger: tcp://east:5565
    images: tcp://east:5567
  outpost:            
    view: PiCamera                 # view name for the camera
    logger: tcp://lab1:5565        # log publisher subscriptions for node
    images: tcp://lab1:5567        # image publisher subscriptions for node
  hilltop:
    view: PiCam3
    logger: tcp://alpha5:5565
    images: tcp://alpha5:5567

# Defines camwatcher to sentinel communication channels. The datapump connection 
# specified below is provided to the sentinel for DataFeed queries. Must match the 
# datapump configuration for this host. 
sentinel:
   requests:  tcp://sentinel:5566      # task requests to sentinel
   publisher: tcp://sentinel:5565      # for subscriptions to sentinel publisher 
   datapump:  tcp://data1:5556         # datapump connection for use by sentinel
   datasink:  data1                    # tag identifying this data sink

       ##   Data storage locations  ##
data:  
  images:   /home/ops/sentinelcam/images      # JPG file storage
  csvfiles: /home/ops/sentinelcam/camwatcher  # CSV file storage

logconfigs: 
    # Internal logging for the camwatcher along with messages from outpost nodes
    camwatcher_internal:
        version: 1
        formatters:
            default:
                format: '%(asctime)s %(levelname)s: %(message)s'
        handlers:
            file:
                class: logging.handlers.RotatingFileHandler
                filename: /home/ops/sentinelcam/logs/camwatcher.log
                formatter: default
                maxBytes: 524288
                backupCount: 10
                level: INFO
        root:
            handlers: [file]
            level: INFO

    # The sentinel does not write to a logfile on disk. All logging activity, 
    # including full task results along with internal warnings and errors, are
    # published over 0MQ. A sentinel agent, as a child subprocess, subscribes to 
    # this and manages updates to the data sink. Other collected status and logging 
    # content will be captured as specified below. 
    sentinel_agent: 
        version: 1
        formatters:
            default:
                format: '%(asctime)s %(levelname)s: %(message)s'
        handlers:
            file:
                class: logging.handlers.TimedRotatingFileHandler
                filename: /home/ops/sentinelcam/logs/sentinel.log
                formatter: default
                when: midnight
                backupCount: 120
                level: INFO
        root:
            handlers: [file]
            level: INFO
```

## Integration

### With ImageHub

CamWatcher subscribes to ImageHub's ZeroMQ stream to monitor camera activity and detect events.

### With DataPump

CamWatcher sends event notifications to DataPump for storage and processing via TCP control port.

### With ImageNodes

CamWatcher can send capture commands to ImageNodes via control messages to trigger specific capture modes.

## Troubleshooting

### Service Won't Start

```bash
# Check logs
ssh data1 'sudo journalctl -u camwatcher -n 50'

# Common issues:
# - Port already in use: Check if another service is using 5566
# - ImageHub not running: Verify imagehub service is active
# - Permission denied: Check file ownership in camwatcher directory
```

### Events Not Detected

```bash
# Check if receiving images from hub
ssh data1 'sudo journalctl -u camwatcher -f | grep "Received"'

# Verify ImageHub is publishing
ssh data1 'sudo journalctl -u imagehub -f'

# Check camera nodes are sending
ssh alpha5 'sudo systemctl status imagenode'
```

### Control Port Issues

```bash
# Check if port is listening
ssh data1 'sudo netstat -tlnp | grep 5566'

# Test connectivity
ssh data1 'nc -zv localhost 5566'
```

## Tags

- `deploy` - Deploy code only (fast)
- `config` - Deploy configuration files
- `service` - Manage systemd service
- `status` - Check service status

## See Also

- Main ansible README: `devops/ansible/README.md`
- CamWatcher documentation: `camwatcher/README.rst`
- Outpost Registry Pattern: `devops/ansible/OUTPOST_REGISTRY_PATTERN.md`
- DataPump role: `roles/datapump/README.md` (sibling service)
- ImageHub role: `roles/imagehub/README.md` (image collection)
- ImageNode role: `roles/imagenode/README.md` (outpost cameras)
- Sentinel role: `roles/sentinel/README.md` (AI processing)
