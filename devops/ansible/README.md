# SentinelCam Ansible Deployment

Ansible automation for deploying and managing SentinelCam distributed camera surveillance infrastructure.

## Quick Start

```bash
# From development workstation - deploy code changes to repository staging area
python devops/scripts/sync/deploy.py <service_name>

# From ramrod control node - deploy to production nodes
cd ~/sentinelcam/devops/ansible
ansible-playbook playbooks/deploy-<service>.yaml

# Code-only deployment (most common for development)
ansible-playbook playbooks/deploy-<service>.yaml --tags deploy
```

## Architecture

### Service Roles

- **sentinelcam_base** - Base provisioning for all nodes (users, Python venv, system packages)
- **camwatcher** - Event monitoring and image capture coordination (datasinks)
- **datapump** - Data retrieval and storage management (datasinks)
- **imagehub** - ZeroMQ image aggregation hub (datasinks)
- **imagenode** - Camera capture and publishing (outposts)
- **sentinel** - AI processing and inference tasks (sentinels)
- **watchtower** - System monitoring and health checks (watchtowers)
- **bastion** - Network gateway, VPN, DNS, firewall (infrastructure - manual only)

### Infrastructure Groups

```yaml
outposts:        # Camera nodes running imagenode
  - lab1         # Legacy pi user
  - east         # Modern ops user
  - alpha5       # Modern ops user

datasinks:       # Data retrieval running camwatcher/datapump/imagehub
  - data1        # Modern ops user

sentinels:       # ML pipeline and model inference running sentinel
  - sentinel     # Legacy pi user

watchtowers:     # Monitoring running watchtower
  - wall1        # Legacy pi user

infrastructure:  # Network services (manual deployment only)
  - chandler-gate  # Rocky Linux, rocky user
```

## Deployment Patterns

### Tag-Based Deployment (Recommended)

Single playbook per service, use tags to control scope:

```bash
# Full setup (initial deployment, config changes, service updates)
ansible-playbook playbooks/deploy-<service>.yaml

# Code only (most common - just application code changes)
ansible-playbook playbooks/deploy-<service>.yaml --tags deploy

# Config only (just configuration file changes)
ansible-playbook playbooks/deploy-<service>.yaml --tags config
```

### Available Playbooks

```bash
# Data sink services
playbooks/deploy-camwatcher.yaml      # Event monitoring
playbooks/deploy-datapump.yaml        # Data management
playbooks/deploy-imagehub.yaml        # Image aggregation

# Outpost services
playbooks/deploy-outpost.yaml         # ImageNode deployment

# AI and ML processing
playbooks/deploy-sentinel.yaml        # Inference tasks

# Monitoring
playbooks/deploy-watchtower.yaml      # Health checks

# Infrastructure (manual only)
playbooks/deploy-bastion.yaml --ask-vault-pass  # Network/VPN/DNS/firewall
```

## Configuration Management

### Variable Precedence

Configuration is loaded in this order (later overrides earlier):

1. `inventory/group_vars/all/` - Global defaults (CANONICAL)
   - `sentinelcam_ports.yaml` - Service port definitions
   - `sentinelcam_standards.yaml` - Paths, Python configs, code deployment
   - `site.yaml` - Site-specific settings
2. `inventory/group_vars/<group>.yaml` - Group-specific vars
3. `inventory/host_vars/<hostname>.yaml` - Node-specific configs
4. `roles/<role>/defaults/main.yaml` - Role defaults
5. Playbook vars

### Key Configuration Files

```
ansible/
  inventory/
    production.yaml                  # Node inventory (IPs, users, groups)
    group_vars/                      # Must be in inventory/ for file-based inventory
      all/
        sentinelcam_ports.yaml       # Port assignments (CANONICAL)
        sentinelcam_standards.yaml   # System-wide standards
        site.yaml                    # Site settings
      datasinks.yaml                 # Data sink nodes top-level configuration
      legacy_nodes.yaml              # Legacy nodes (pi user) overrides
    host_vars/                       # Must be in inventory/ for file-based inventory
      <hostname>.yaml                # Per-node configuration (camera type, etc.)
```

### Port Assignments

Defined in `group_vars/all/sentinelcam_ports.yaml`:

```yaml
sentinelcam_ports:
  imagehub_zmq: 5555              # ImageHub ZeroMQ PUB
  datapump_control: 5556          # DataPump TCP control
  imagenode_logging: 5565         # ImageNode log publishing
  camwatcher_control: 5566        # CamWatcher TCP control
  sentinel_control: 5566          # Sentinel TCP control
  imagenode_publisher: 5567       # ImageNode image publishing
```

## Development Workflow

### 1. Local Development (Development Workstation running Windows/Linux/macOS)

```bash
# Edit code in repository
code sentinelcam/

# Stage changes to data1 (current_deployment/)
python devops/scripts/sync/deploy.py <service>
# Examples:
python devops/scripts/sync/deploy.py camwatcher
python devops/scripts/sync/deploy.py imagenode
python devops/scripts/sync/deploy.py sentinel
```

### 2. Deploy to Production (from the ramrod node)

```bash
# SSH to ramrod control node, then
cd ~/sentinelcam/devops/ansible

# Code-only deployment (fast, most common)
ansible-playbook playbooks/deploy-<service>.yaml --tags deploy

# Full deployment (config + code)
ansible-playbook playbooks/deploy-<service>.yaml

# Limit to specific node
ansible-playbook playbooks/deploy-<service>.yaml --limit <hostname>
```

### 3. Verification

```bash
# Check service status
ssh <target_node> 'sudo systemctl status <service>'

# View logs
ssh <target_node> 'sudo journalctl -u <service> -f'
```

## Role Documentation

Each role has detailed documentation in its directory:

- `roles/sentinelcam_base/README.md` - Base provisioning
- `roles/camwatcher/README.md` - Event monitoring
- `roles/datapump/README.md` - Data management
- `roles/imagehub/README.md` - Image aggregation
- `roles/imagenode/README.md` - Camera capture (includes model deployment)
- `roles/sentinel/README.md` - AI inference (includes model deployment)
- `roles/watchtower/README.md` - System monitoring
- `roles/bastion/README.md` - Network infrastructure

## Common Tasks

### Deploy Code Changes

```bash
# From development workstation
python devops/scripts/sync/deploy.py camwatcher

# From ramrod control node
ansible-playbook playbooks/deploy-camwatcher.yaml --tags deploy
```

### Update Configuration

```bash
# Edit inventory/host_vars/<hostname>.yaml or inventory/group_vars
# Then deploy with config tag
ansible-playbook playbooks/deploy-outpost.yaml --tags config --limit alpha5
```

### New Outpost Setup

```bash
# 1. Add to inventory/production.yaml
# 2. Create inventory/host_vars/<hostname>.yaml
# 3. Deploy
ansible-playbook playbooks/deploy-outpost.yaml --limit <new_node>
```

### Restart Services

```bash
# Via Ansible
ansible <group> -m systemd -a "name=<service> state=restarted" --become

# Direct SSH
ssh <node> 'sudo systemctl restart <service>'
```

## Troubleshooting

### Undefined Variable Errors

Check variable precedence - ensure variables are defined in `inventory/group_vars/all/` (canonical source for ports and standards).

### Permission Denied

Verify `ansible_user` in inventory matches actual user on target node:
- Modern nodes (east, alpha5, data1): `ops` user
- Legacy nodes (lab1, sentinel, wall1): `pi` user

### Service Won't Start

```bash
# Check service status
ssh <node> 'sudo systemctl status <service>'

# View detailed logs
ssh <node> 'sudo journalctl -u <service> -n 100'

# Validate config file
ssh <node> 'python3 -c "import yaml; yaml.safe_load(open(\"/home/<user>/<service>.yaml\"))"'
```

### Code Not Deploying

Verify staging area on data1:
```bash
ssh data1 'ls -la /home/ops/sentinelcam/current_deployment/<service>/'
```

If missing, run `python devops/scripts/sync/deploy.py <service>` from development workstation.

## Infrastructure Notes

### User Migration

System is transitioning from `pi` to `ops` user:
- **Complete**: east, alpha5, data1
- **Pending**: lab1, sentinel, wall1

Ansible handles this automatically via `ansible_user` in inventory.

### Code Deployment Flow

```
Development workstation (repository)
  ↓ deploy.py script
data1:/home/ops/sentinelcam/current_deployment/  (staging)
  ↓ sentinelcam_base role (--tags deploy)
Target node:/home/<ansible_user>/<service>/  (production)
```

### Network Architecture

- Internal network: 192.168.10.0/24
- Gateway/DNS: chandler-gate (192.168.10.254)
- Control node: buzz (192.168.10.10)
- ZeroMQ pub/sub messaging for image distribution
- TCP control ports for service coordination

## Contributing

When adding new services:

1. Create role in `roles/<service>/`
2. Add playbook to `playbooks/deploy-<service>.yaml`
3. Define ports in `inventory/group_vars/all/sentinelcam_ports.yaml`
4. Add node config to `inventory/host_vars/<hostname>.yaml`
5. Document in `roles/<service>/README.md`

## License

See LICENSE file in repository root.
