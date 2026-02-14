# ImageHub Role

Deploys and manages **imagehub** on datasink nodes.

## Purpose

ImageHub is the companion receiver for **imagenode**. It collects images and event messages from multiple
imagenode camera sources simultaneously over ZeroMQ REQ/REP messaging, storing them to disk. While not a
SentinelCam-specific component, it is required infrastructure for the outpost camera pipeline — handling
image and sensor capture that falls outside the SentinelCam event-driven workflow.

## Dependencies

- **sentinelcam_base** — user, venv, directory setup
- **imagenode** — source nodes that capture and send images

## Configuration

Service-level variables are defined in `group_vars/datasinks.yaml`. The application config template
generates `imagehub.yaml` from role defaults — key settings include queue depth, patience interval,
data directory, and daily image caps.

## Deployment

```bash
# Full setup
ansible-playbook playbooks/deploy-imagehub.yaml

# Code update
ansible-playbook playbooks/deploy-imagehub.yaml --tags deploy

# Config change
ansible-playbook playbooks/deploy-imagehub.yaml --tags config
```

## Tags

| Tag | Scope |
|-----|-------|
| `deploy` | Application code sync |
| `config` | Generate and deploy imagehub.yaml + systemd unit |
| `service` | Systemd service management |
| `status` | Check service state |

## File Structure (on target)

```
/home/<sentinelcam_user>/
├── imagehub.yaml              # Application configuration
├── imagehub/                  # Python package
│   ├── imagehub.py
│   └── tools/
└── sentinelcam/
    └── imagehub/              # Data storage
        ├── images/            # JPEG collections by date (YYYY-MM-DD/)
        └── logs/
```

## See Also

- [ImageHub upstream](https://github.com/jeffbass/imagehub)
- [ImageZMQ](https://github.com/jeffbass/imagezmq)
