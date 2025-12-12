# SentinelCam Ansible Deployment

Ansible automation for SentinelCam software deployment, server configuration, and management on distributed infrastructure.

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
- **camwatcher** - Subscriber for outpost event logging and image capture publication (datasinks)
- **datapump** - Data retrieval engine, and storage management (datasinks)
- **imagehub** - Aggregation hub for imagenode requests with data/image payload (datasinks)
- **imagenode** - Camera image and event publishing (outposts)
- **sentinel** - AI processing and inference tasks (sentinels)
- **watchtower** - Wall console, outpost and event viewer (watchtowers)
- **bastion** - Network gateway, VPN, DNS, firewall (infrastructure - manual only)

### Infrastructure Groups

```yaml
outposts:        # Outpost camera nodes running imagenode
  - lab1
  - east
  - alpha5

datasinks:       # Data retrieval running camwatcher/datapump/imagehub
  - data1

sentinels:       # ML pipeline and model inference running sentinel
  - sentinel

watchtowers:     # Wall console touch screen displays running watchtower
  - wall1

infrastructure:  # Network services (manual deployment only)
  - chandler-gate
```

## Deployment Patterns

### Tag-Based Deployment (Recommended)

Single playbook per service, use tags to control scope:

```bash
# Full setup (initial provisioning, code deployment, configuration, service definitions)
ansible-playbook playbooks/deploy-<service>.yaml

# Code only (most common - just application code changes)
ansible-playbook playbooks/deploy-<service>.yaml --tags deploy

# Configuration change only (this example limited to a specific node)
ansible-playbook playbooks/deploy-<service>.yaml --tags config --limit <hostname>
```

### Available Playbooks

```bash
# Data sink services
playbooks/deploy-camwatcher.yaml      # Output subscriber
playbooks/deploy-datapump.yaml        # DataFeed request servicing
playbooks/deploy-imagehub.yaml        # Image/sensor aggregation

# Outpost services
playbooks/deploy-outpost.yaml         # ImageNode deployment

# AI and ML processing
playbooks/deploy-sentinel.yaml        # Inference tasks

# Wall consoles
playbooks/deploy-watchtower.yaml      # Live viewer and event replay

# Infrastructure (manual only)
playbooks/deploy-bastion.yaml --ask-vault-pass  # Network/VPN/DNS/firewall
```

## Configuration Management

### Variable Precedence

Configuration is loaded in this order (later overrides earlier):

1. `inventory/group_vars/all/` - Global defaults (CANONICAL)
   - `model_registry.yaml` - Model repository version selections
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
    group_vars/
      all/
        model_registry.yaml          # Selections for model versions to be deployed
        sentinelcam_ports.yaml       # Port assignments (CANONICAL)
        sentinelcam_standards.yaml   # System-wide standards
        site.yaml                    # Site settings
      datasinks.yaml                 # Data sink nodes top-level configuration
      legacy_nodes.yaml              # Legacy nodes (pi user) overrides
    host_vars/
      <hostname>.yaml                # Per-node configuration (camera type, etc.)
```

## Role Documentation

Each role has detailed documentation in its directory:

- [`roles/sentinelcam_base/README.md`](../ansible/roles/sentinelcam_base/README.md) - Base provisioning
- [`roles/camwatcher/README.md`](../ansible/roles/camwatcher/README.md) - Event monitoring
- [`roles/datapump/README.md`](../ansible/roles/datapump/README.md) - Data management
- [`roles/imagehub/README.md`](../ansible/roles/imagehub/README.md) - Image aggregation
- [`roles/imagenode/README.md`](../ansible/roles/imagenode/README.md) - Camera capture (includes model deployment)
- [`roles/sentinel/README.md`](../ansible/roles/sentinel/README.md) - AI inference (includes model deployment)
- [`roles/watchtower/README.md`](../ansible/roles/watchtower/README.md) - System monitoring
- [`roles/bastion/README.md`](../ansible/roles/bastion/README.md) - Network infrastructure

## Further Reading

- [Code deployment pattern](../docs/configuration/CODE_DEPLOYMENT_PATTERN.md)
- [Outpost registry pattern](../docs/configuration/OUTPOST_REGISTRY_PATTERN.md)
- [Packaged based code deployment tool](../scripts/sync/README.md)
- [Automated CI/CD code deployment pipeline](../README.rst)

### Troubleshooting

For comprehensive troubleshooting guidance, see the [SentinelCam Troubleshooting Guide](../docs/troubleshooting/SENTINELCAM_TROUBLESHOOTING_GUIDE.md).

## License

See LICENSE file in repository root.
