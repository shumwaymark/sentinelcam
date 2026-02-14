# SentinelCam Base Role

Foundation provisioning for all SentinelCam nodes. Included as a dependency by every component role.

## Purpose

Creates the common environment that all SentinelCam services require: service user/group, Python virtual environment
(with `--system-site-packages` for hardware access), directory structure, and optional code deployment via rsync
from the primary datasink.

## Dependencies

None. This is the foundational role.

## Configuration

Most variables are defined in `group_vars/all/sentinelcam_standards.yaml`. Key overrides:

| Variable | Purpose |
|----------|---------|
| `deploy_source_code` | Enable hub-and-spoke code sync from primary datasink (default: `false`) |
| `skip_base_provisioning` | Skip user/venv creation for code-only updates (default: `false`) |
| `sentinelcam_requirements_file` | Override default requirements.txt filename from role `files/` |

### Code Deployment Model

When `deploy_source_code: true`, rsync distributes code from primary datasink to all nodes:
- **Primary datasink** deploys locally from `code_source_path`
- **All other nodes** pull via rsync with Ansible delegation
- Handlers restart affected services automatically

## Deployment

```bash
# Full provisioning (new node)
ansible-playbook playbooks/deploy-<service>.yaml

# Code-only update
ansible-playbook playbooks/deploy-<service>.yaml --tags deploy

# Python dependency update (after editing files/requirements.txt)
ansible-playbook playbooks/deploy-<service>.yaml --tags python_deps
```

## Tags

| Tag | Scope |
|-----|-------|
| `system_deps` | OS-level packages (picamera2 on modern nodes) |
| `venv` | Virtual environment creation |
| `python_deps` | pip install from requirements.txt |
| `deploy` / `code_deployment` | Source code rsync |
| `optimization` | System tuning (tmpfiles, sysctl) |

## File Structure (on target)

```
/home/<sentinelcam_user>/
├── .opencv/                 # Python virtual environment
├── sentinelcam/             # Base data directory
│   └── logs/                # Application logs
└── <component>/             # Deployed application code (per role)
```

## See Also

- [Ansible README](../../README.md) — deployment patterns and variable precedence
- [Code Deployment Pattern](../../../docs/configuration/CODE_DEPLOYMENT_PATTERN.md)
- [Standards](../../inventory/group_vars/all/sentinelcam_standards.yaml) — canonical path and user definitions

