# ImageHub Role

Deploys and manages the **imagehub** service on datasink nodes.

## Purpose

Although **imagehub** does not currently have a specific role within SentinelCam, it is a required companion process to
**imagenode** which is the foundation of the SentinelCam outposts. This module receives and stores images and event messages
from multiple imagenode camera sources simultaneously using ZeroMQ REQ/REP messaging. It acts as the central image and message
collection hub for sensor and image capture which fall outside SentinelCam use cases. 

## Supported Nodes

- **DataSinks**: data1 (any node in `datasinks` group)

## Dependencies

- **sentinelcam_base**: Must run first to provision Python environment and system packages
- **imagenode**: Source nodes that capture and send images and event messages via ZeroMQ
 
## Variables

### Core Configuration (group_vars/all/sentinelcam_ports.yaml)

```yaml
sentinelcam_ports:
  imagehub_zmq: 5555               # ZeroMQ REP socket for image reception
```

### Service Configuration (group_vars/datasinks.yaml)

```yaml
imagehub_service: imagehub
imagehub_config_file: imagehub.yaml
imagehub_install_path: /home/ops/imagehub
imagehub_config_path: /home/ops/imagehub.yaml

```

### Optional Overrides

```yaml
data_directory: "{{ sentinelcam_directories.imagehub_data }}"
```

## Deployment

### Initial Setup

```bash
# Deploy full service (includes config, systemd, code)
ansible-playbook playbooks/deploy-imagehub.yaml
```

### Code Updates (Most Common)

```bash
# From Windows workstation
python devops/scripts/sync/deploy.py imagehub

# From buzz
ansible-playbook playbooks/deploy-imagehub.yaml --tags deploy
```

### Configuration Updates

```bash
# Edit config, then deploy
ansible-playbook playbooks/deploy-imagehub.yaml --tags config
```

## File Structure

```
/home/<sentinelcam_user>/
├── imagehub.yaml          # application configuration
└── imagehub/              # Python package
    ├── imagehub.py        # Main application
    └── tools/             # Utilities
```

## Service Management

```bash
# Check status
sudo systemctl status imagehub

# View logs
sudo journalctl -u imagehub -f

# Restart service
sudo systemctl restart imagehub
```

## Architecture

ImageHub is designed to be simple and fast - it has two primary functions:

1. **Receive and store images** from multiple imagenode sources
2. **Receive and log event messages** from those sources

This simplicity allows it to reliably handle simultaneous connections from multiple imagenode sources without dropping frames 
or events. All image analysis and event processing is handled by external services, such as **librarian**.

## ZeroMQ Communication

ImageHub uses ZeroMQ REP socket pattern to receive images:

- **Protocol**: ZeroMQ REP/REQ
- **Port**: 5555 (configurable via `sentinelcam_ports.imagehub_zmq`)
- **Message Format**: Tuple of (text, image)
- **Response**: 'OK' acknowledgment to sender

Multiple imagenode instances connect to ImageHub's REP socket to send images captured by their cameras.

## Image Storage

Images are stored in a date-based directory structure:

```
/home/<sentinelcam_user>/
└── sentinelcam/             # SSD mount point
    └── imagehub/            # imagehub data folder
        ├── images/          # image collection folders by date
        │   └── YYYY-MM-DD/  
        └── logs/            # imagehub logging
```

## Configuration Example

Minimal `imagehub.yaml`:

```yaml
# Settings file imagehub.yaml -- sentinelcam test #1
---
hub:
  queuemax: 500 # maximum size of queue of images to write
  patience: 1  # how often to log a lack of message in minutes
  print_settings: False
  data_directory: /home/ops/sentinelcam/imagehub
  max_images_write: 7500  # a cap on images to write in one day
```

## Related Services

- **imagenode**: Camera capture nodes that send images to **imagehub**

## Troubleshooting

### No Images Received

Check that **imagenode** services are running and can reach **imagehub**:

```bash
# From imagenode host
telnet data1 5555
```

### Port Already in Use

```bash
# Check what's using port 5555
sudo ss -tulpn | grep 5555
```

### Image Storage Full

Monitor disk space on datasink:

```bash
df -h ~/sentinelcam
```

Consider implementing image rotation/archival policies.

## Performance Notes

- ImageHub can handle multiple simultaneous imagenode connections
- Datasink nodes should use fast storage (NVMe SSD preferred)

## References

- [ImageHub GitHub Repository](https://github.com/jeffbass/imagehub)
- [ImageZMQ Documentation](https://github.com/jeffbass/imagezmq)
