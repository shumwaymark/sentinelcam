# DataPump Role

Deploys and manages **datapump** on datasink nodes.

## Purpose

DataPump is the data retrieval and storage management service for SentinelCam. The `DataFeed` library
provides the API that sentinel, watchtower, and external tools use to access event data, images, and
tracking results. Also manages face data deployment (facelist CSV from the model registry) and automated
model registry cleanup.

## Dependencies

- **sentinelcam_base** — user, venv, directory setup
- **camwatcher** — sibling service; data capture source

## Configuration

Service-level variables are defined in `group_vars/datasinks.yaml`. The application config template
generates `datapump.yaml` from role defaults.

| Variable | Source | Purpose |
|----------|--------|---------|
| `datapump_service` | `group_vars/datasinks.yaml` | Service name |
| `datapump_install_path` | `group_vars/datasinks.yaml` | Install directory |
| `faces_data_file` | `defaults/main.yaml` | Versioned facelist CSV path |
| `debug_mode` | host_vars (optional) | Enable debug logging |

### Face Data

The role deploys the current facelist CSV from the model registry to the datasink. Version is
controlled via `group_vars/all/model_registry.yaml`.

### Model Registry Cleanup

A systemd timer runs weekly (Sunday 3 AM) to prune old model versions. Retention: current + previous
+ 5 most recent.

## Deployment

```bash
# Full setup
ansible-playbook playbooks/deploy-datapump.yaml

# Code update
ansible-playbook playbooks/deploy-datapump.yaml --tags deploy

# Config change
ansible-playbook playbooks/deploy-datapump.yaml --tags config

# Face data update (after model registry change)
ansible-playbook playbooks/deploy-datapump.yaml --tags faces
```

## Tags

| Tag | Scope |
|-----|-------|
| `deploy` | Application code sync |
| `config` | Generate and deploy datapump.yaml + systemd unit |
| `service` | Systemd service management |
| `faces` | Deploy facelist CSV from model registry |
| `model_registry` / `cleanup` | Model registry cleanup timer |
| `status` | Check service state |

## File Structure (on target)

```
/home/<sentinelcam_user>/
├── datapump.yaml              # Application configuration
├── camwatcher/
│   └── datapump/              # Python package (datapump.py, sentinelcam/)
└── sentinelcam/
    ├── camwatcher/            # CSV event data
    ├── images/                # JPEG storage
    ├── faces/                 # Facelist CSV (deployed from registry)
    └── model_registry/        # Centralized model storage (datasinks only)
```

## See Also

- [CamWatcher role](../camwatcher/README.md) — sibling service, shared deployment
- [Model Registry](../../../docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md)
- [Sentinel role](../sentinel/README.md) — primary DataFeed consumer

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
