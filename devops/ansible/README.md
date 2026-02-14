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

## Service Roles

Each role has detailed documentation in its directory:

| Component | Role README | Purpose |
|-----------|-------------|---------|
| sentinelcam_base | [roles/sentinelcam_base/](roles/sentinelcam_base/README.md) | Foundation provisioning (all nodes) |
| imagenode | [roles/imagenode/](roles/imagenode/README.md) | Outpost cameras and edge inference |
| camwatcher | [roles/camwatcher/](roles/camwatcher/README.md) | Outpost subscriber (datasink) |
| datapump | [roles/datapump/](roles/datapump/README.md) | Data retrieval API (datasink) |
| imagehub | [roles/imagehub/](roles/imagehub/README.md) | Image collection (datasink) |
| sentinel | [roles/sentinel/](roles/sentinel/README.md) | AI/ML processing and inference |
| watchtower | [roles/watchtower/](roles/watchtower/README.md) | Wall console, outpost and event viewer |
| deepthink | [roles/deepthink/](roles/deepthink/README.md) | ML training node (Jetson Nano) |
| bastion | [roles/bastion/](roles/bastion/README.md) | Network gateway/VPN (infrastructure) |
| infrastructure | [roles/infrastructure/](roles/infrastructure/README.md) | DNS management (infrastructure) |

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

## Further Reading

- [Code deployment pattern](../docs/configuration/CODE_DEPLOYMENT_PATTERN.md)
- [Outpost registry pattern](../docs/configuration/OUTPOST_REGISTRY_PATTERN.md)
- [Packaged based code deployment tool](../scripts/sync/README.md)
- [Automated CI/CD code deployment pipeline](../README.rst)

### Troubleshooting

For comprehensive troubleshooting guidance, see the [SentinelCam Troubleshooting Guide](../docs/troubleshooting/SENTINELCAM_TROUBLESHOOTING_GUIDE.md).

## License

See LICENSE file in repository root.
