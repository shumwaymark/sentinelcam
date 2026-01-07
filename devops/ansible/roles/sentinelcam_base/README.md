# SentinelCam Base Role

## Overview

The `sentinelcam_base` role provides the foundation provisioning for all SentinelCam nodes. It establishes the common infrastructure required by all other SentinelCam roles, including user management, Python virtual environment setup, directory structure, and optional code deployment.

This role is designed to be included as a dependency by all component-specific roles (imagenode, camwatcher, sentinel, etc.).

## Features

- **User Management**: Creates the SentinelCam service user and group
- **Python Environment**: Sets up a virtual environment with `--system-site-packages` for hardware access (e.g., picamera2)
- **Dependency Management**: Deploys and installs Python requirements from version-controlled requirements.txt in role files/
- **Directory Structure**: Creates base data and log directories
- **Code Deployment**: Optional synchronization of source code from the primary datasink to all nodes
- **Service Handlers**: Provides handlers to restart SentinelCam services after code updates

## Requirements

- Ansible 2.9+
- Target systems running Raspberry Pi OS (Debian-based Linux)
- SSH access to target nodes
- Python 3 installed on target systems

## Role Variables

### Required Variables

These variables should be defined in group_vars or host_vars:

```yaml
# User and environment settings
sentinelcam_user: ops                    # Service user account
sentinelcam_group: ops                   # Service group
sentinelcam_venv_path: /home/ops/.opencv # Python virtual environment path

# Base directories
sentinelcam_data_path: /home/ops/sentinelcam  # Base data directory

# Code deployment (optional)
code_source_path: /path/to/source        # Source code directory on primary datasink
deploy_source_code: false                # Enable code deployment
```

### Optional Variables

```yaml
skip_base_provisioning: false            # Skip user/venv creation if already done
sentinelcam_requirements_file: requirements.txt  # Override with alternate requirements file name from files/
```

### Component-Specific Variables

When code deployment is enabled, these paths are used:

```yaml
# Data sink components
camwatcher_install_path: /home/ops/sentinelcam/camwatcher
datapump_install_path: /home/ops/sentinelcam/datapump
imagehub_install_path: /home/ops/sentinelcam/imagehub

# Other component defaults
imagenode_install_path: /home/ops/imagenode
sentinel_install_path: /home/ops/sentinel
watchtower_install_path: /home/pi/watchtower
```

## Dependencies

None. This is the foundational role that other roles depend on.

## Inventory Requirements

The role expects the following inventory groups:

- `datasinks`: Data sink nodes (primary receives local deployment, others sync from primary)
- `outposts`: Camera nodes running imagenode
- `watchtowers`: Watcher nodes running watchtower
- `modern_nodes`: Nodes running newer OS (Bookworm+) requiring python3-picamera2

## Example Playbook

### Basic Usage (Provisioning Only)

```yaml
---
- name: Provision SentinelCam base environment
  hosts: sentinelcam
  become: yes
  roles:
    - sentinelcam_base
```

### With Code Deployment

```yaml
---
- name: Provision and deploy SentinelCam
  hosts: sentinelcam
  become: yes
  vars:
    deploy_source_code: true
    code_source_path: /home/ops/sentinelcam/current_deployment
  roles:
    - sentinelcam_base
```

### Skip Base Provisioning (Code Updates Only)

```yaml
---
- name: Deploy code updates only
  hosts: sentinelcam
  become: yes
  vars:
    skip_base_provisioning: true
    deploy_source_code: true
    code_source_path: /home/ops/sentinelcam/current_deployment
  roles:
    - sentinelcam_base
```

## Tasks Breakdown

### Main Tasks (`tasks/main.yaml`)

1. **User Management**
   - Creates service group and user
   - Sets up home directory and shell
   - Skipped when `skip_base_provisioning: true`

2. **System Dependencies**
   - Installs `python3-picamera2` on modern nodes
   - Required for camera access with system site packages

3. **Python Environment**
   - Creates virtual environment with `--system-site-packages`
   - Deploys requirements.txt from Ansible control host (role files/)
   - Installs Python dependencies on all nodes uniformly
   - Creates activation helper script
   - Supports host-specific requirements via file naming (e.g., requirements_coral.txt)

4. **Directory Structure**
   - Base data directory
   - Logs directory

5. **Code Deployment** (optional)
   - Includes `code_deployment.yaml` when `deploy_source_code: true`

### Code Deployment Tasks (`tasks/code_deployment.yaml`)

Handles source code synchronization with a sophisticated delegation strategy:

- **Primary Datasink**: Deploys code locally from `code_source_path`
- **Other Nodes**: Receive code via rsync from primary datasink
- **Components Synced**:
  - camwatcher, datapump, imagehub (datasinks)
  - imagenode (outposts)
  - sentinel (sentinel nodes)
  - watchtower (watchtowers)

### Handlers (`handlers/main.yaml`)

Service restart handlers triggered by code deployment:

- `restart camwatcher`
- `restart datapump`
- `restart imagehub`
- `restart imagenode`
- `restart sentinel`
- `restart watchtower`

## Tags

Apply specific subsets of tasks using tags with any deployment playbook:

```bash
# User and group creation only
ansible-playbook playbooks/deploy-outpost.yaml --tags users

# Python dependencies only
ansible-playbook playbooks/deploy-datasink.yaml --tags python_deps

# Virtual environment setup
ansible-playbook playbooks/deploy-sentinel.yaml --tags venv

# Directory creation
ansible-playbook playbooks/deploy-watchtower.yaml --tags directories

# Code deployment only
ansible-playbook playbooks/deploy-outpost.yaml --tags code_deployment

# Skip base provisioning (code updates only)
ansible-playbook playbooks/deploy-datasink.yaml --skip-tags system_deps,users,venv,python_deps
```

## Workflow Patterns

### Initial Provisioning

First-time setup of a new node:

```yaml
vars:
  skip_base_provisioning: false
  deploy_source_code: true
```

### Code Updates

Update code without reprovisioning:

```yaml
vars:
  skip_base_provisioning: true
  deploy_source_code: true
```

### Python Dependency Updates

Update requirements.txt in the role files/ directory, commit to Git, then deploy:

```bash
# Update only Python packages across all nodes
ansible-playbook playbooks/deploy-outpost.yaml --tags python_deps
ansible-playbook playbooks/deploy-sentinel.yaml --tags python_deps
ansible-playbook playbooks/deploy-datasink.yaml --tags python_deps

# Or update all nodes at once
ansible-playbook site.yml --tags python_deps
```

## Architecture Notes

### Deployment Strategy

The role implements a hub-and-spoke deployment model:

1. **Development Machine** → **Primary Datasink**: Manual sync using `deploy.py` script
2. **Primary Datasink** → **All Nodes**: Automated via Ansible rsync with delegation

This ensures:
- Single source of truth (primary datasink)
- Consistent code across all nodes
- Efficient synchronization
- No direct development machine access needed for all nodes

### Virtual Environment Design

Uses `--system-site-packages` to provide:
- Access to hardware-specific libraries (picamera2, GPIO)
- Isolated Python dependencies for application code
- Best of both worlds: system integration + dependency isolation

## Common Issues

### Python Dependencies Not Installing

**Symptom**: pip install fails or wrong packages installed

**Solution**: 
- Verify requirements.txt exists in role files/ directory
- Check requirements.txt syntax (package==version format)
- For host-specific requirements, use `sentinelcam_requirements_file` variable to override default

### Code Not Syncing

**Symptom**: Code changes not appearing on target nodes

**Solution**: 
- Verify `deploy_source_code: true` is set
- Check `code_source_path` is correct
- Ensure primary datasink is reachable via SSH
- Verify rsync is installed on all nodes

### Services Not Restarting

**Symptom**: Code deployed but services not restarted

**Solution**: Check that systemd service files exist and handlers are not suppressed

## Version History

- **v2.0**: Added sophisticated code deployment with delegation strategy
- **v1.5**: Separated base provisioning from code deployment
- **v1.0**: Initial release with user/venv setup

## License

See project LICENSE file

## Author

Mark Shumway / SentinelCam Project

