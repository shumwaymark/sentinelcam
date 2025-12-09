# SentinelCam Deployment Overview

**Last Updated**: December 6, 2025

## Introduction

SentinelCam uses Ansible for automated deployment across all node types. Each component has a dedicated role with comprehensive documentation. This overview provides a roadmap to the deployment documentation.

## Deployment Architecture

```
Development Machine
    ↓ (deploy.py)
Primary Datasink (data1)
    ↓ (ansible)
All Nodes (via sentinelcam_base role)
```

**Key Principles**:
- **Hub-and-spoke**: Primary datasink is the deployment hub
- **Role-based**: Each component has a dedicated Ansible role
- **Incremental**: Provision once, update code frequently
- **Consistent**: Same process across all node types

## Component Deployment Guides

### 📹 Outpost (Camera Nodes)

**Component**: ImageNode  
**Documentation**: [`roles/imagenode/README.md`](../../ansible/roles/imagenode/README.md)

**What it does**: Captures images from cameras (PiCamera, OAK/DepthAI, Webcam), performs edge detection, publishes to datasinks via ZeroMQ.

**Quick deploy**:
```bash
# Initial setup
ansible-playbook playbooks/deploy-outpost.yaml --limit <hostname>

# Code updates
python devops/scripts/sync/deploy.py imagenode
```

**Key features**:
- Unified template for all camera types (P*, O*, W*)
- Auto-resolves datasink from outpost registry
- Hardware acceleration support (NCS2, Coral)
- Multi-camera support

### 💾 Datasink (Data Processing Nodes)

Datasinks run three services: CamWatcher, DataPump, and ImageHub.

#### CamWatcher

**Documentation**: [`roles/camwatcher/README.md`](../../ansible/roles/camwatcher/README.md)

**What it does**: Monitors imagenode streams, coordinates event-driven capture, manages event detection/notification.

**Quick deploy**:
```bash
ansible-playbook playbooks/deploy-camwatcher.yaml
```

**Key features**:
- Auto-subscribes to assigned outposts from registry
- Event detection and sentinel coordination
- ZeroMQ pub/sub architecture

#### DataPump

**Documentation**: [`roles/datapump/README.md`](../../ansible/roles/datapump/README.md)

**What it does**: Data retrieval and storage management, provides DataFeed API for accessing stored events.

**Quick deploy**:
```bash
ansible-playbook playbooks/deploy-datapump.yaml
```

**Key features**:
- Event storage and retrieval
- Face database management
- Historical data queries

#### ImageHub

**Documentation**: [`roles/imagehub/README.md`](../../ansible/roles/imagehub/README.md)

**What it does**: Receives and stores images from multiple imagenode sources via ZeroMQ REQ/REP pattern.

**Quick deploy**:
```bash
ansible-playbook playbooks/deploy-imagehub.yaml
```

**Key features**:
- Simple, fast, reliable image collection
- Multi-source support
- Date-based storage structure

### 🤖 Sentinel (AI Processing Node)

**Component**: Sentinel  
**Documentation**: [`roles/sentinel/README.md`](../../ansible/roles/sentinel/README.md)

**What it does**: AI inference tasks including face detection, face recognition, object classification. Hardware acceleration via Intel NCS2 or Google Coral.

**Quick deploy**:
```bash
ansible-playbook playbooks/deploy-sentinel.yaml
```

**Key features**:
- Multiple task engines (Alpha, Bravo1, Bravo2)
- Priority-based processing
- Configurable task chains
- Hardware accelerator support

### 📺 Watchtower (Display/Monitoring Nodes)

**Component**: Watchtower  
**Documentation**: [`roles/watchtower/README.md`](../../ansible/roles/watchtower/README.md)

**What it does**: Live view display for wall-mounted touchscreen consoles, shows real-time camera feeds and AI detection events.

**Quick deploy**:
```bash
ansible-playbook playbooks/deploy-watchtower.yaml
```

**Key features**:
- Auto-generated from outpost registry
- Touch-optimized UI for RPi touchscreen
- Real-time ZeroMQ subscriptions
- Ring buffer architecture

## Deployment Workflows

### Initial Node Setup

1. **Add to inventory** (`inventory/production.yaml`)
2. **Create inventory/host_vars** (if node-specific config needed)
3. **Run full deployment playbook**

Example for new outpost:
```bash
# 1. Add to inventory
vim inventory/production.yaml

# 2. Create host_vars with camera config
vim inventory/host_vars/new_outpost.yaml

# 3. Deploy
ansible-playbook playbooks/deploy-outpost.yaml --limit new_outpost
```

### Code Updates (Most Common)

From development workstation:
```bash
# Sync to primary datasink, then deploy to nodes
python devops/scripts/sync/deploy.py <component>

# Components: imagenode, camwatcher, datapump, imagehub, sentinel, watchtower
```

From control node (buzz):
```bash
# Deploy directly via ansible
ansible-playbook playbooks/deploy-<component>.yaml --tags deploy
```

### Configuration Updates

```bash
# Edit configuration
vim host_vars/<hostname>.yaml
# or
vim group_vars/<group>.yaml

# Deploy config only (fast)
ansible-playbook playbooks/deploy-<component>.yaml --tags config --limit <hostname>
```

### Service Management

```bash
# Check service status
ansible <hostname> -m systemd -a "name=<service>" -b

# Restart service
ansible <hostname> -m systemd -a "name=<service> state=restarted" -b

# View logs
ansible <hostname> -m command -a "journalctl -u <service> -n 50" -b
```

## Configuration Patterns

### Outpost Registry

All outpost-to-datasink mappings are centralized in `inventory/group_vars/all/site.yaml`:

```yaml
sentinelcam_outposts:
  <outpost_hostname>:
    datasink: <datasink_hostname>
    cameras:
      <camera_id>:
        viewname: <view_name>
        description: "Camera description"
        resolution: [width, height]
```

**See**: [`devops/ansible/OUTPOST_REGISTRY_PATTERN.md`](../../ansible/OUTPOST_REGISTRY_PATTERN.md) for complete documentation.

### Port Assignments

All service ports centralized in `inventory/group_vars/all/sentinelcam_ports.yaml`:

```yaml
sentinelcam_ports:
  imagehub_zmq: 5555
  datapump_control: 5556
  camwatcher_control: 5566
  sentinel_requests: 5566
  sentinel_publisher: 5565
  imagenode_publisher: 5567
  imagenode_logging: 5565
```

### Directory Structure

Standard paths in `inventory/group_vars/all/sentinelcam_standards.yaml`:

```yaml
sentinelcam_directories:
  images: /home/ops/sentinelcam/images
  csvfiles: /home/ops/sentinelcam/camwatcher
  logs: /home/ops/sentinelcam/logs
```

## Common Issues & Solutions

### Service Won't Start

```bash
# Check logs
ansible <hostname> -m command -a "journalctl -u <service> -n 50" -b

# Common issues:
# - Port conflicts: Check netstat
# - Permission errors: Check file ownership
# - Missing dependencies: Re-run provisioning
```

### Code Not Deploying

```bash
# Verify primary datasink has latest code
ssh ops@data1 'ls -la /home/ops/sentinelcam/current_deployment/'

# Check rsync connectivity
ansible <hostname> -m ping

# Force code deployment
ansible-playbook playbooks/deploy-<component>.yaml --tags code_deployment
```

### Configuration Not Applied

```bash
# Verify host_vars loaded
ansible-inventory --host <hostname> --yaml

# Check variable precedence
ansible <hostname> -m debug -a "var=<variable_name>"

# Re-deploy config
ansible-playbook playbooks/deploy-<component>.yaml --tags config --limit <hostname>
```

## Testing Deployments

### Validation Checklist

Before deploying to production:
1. ✅ Test in development environment
2. ✅ Run with `--check` flag (dry-run)
3. ✅ Review changes with `--diff`
4. ✅ Limit to single node first
5. ✅ Verify service restarts cleanly
6. ✅ Check logs for errors

See: [`operations/VALIDATION_CHECKLIST.md`](../operations/VALIDATION_CHECKLIST.md)

### Rollback Procedure

```bash
# Services automatically create backups
# Check backup location
ssh <hostname> 'ls -la /home/ops/*.yaml.backup*'

# Restore previous config
ssh <hostname> 'sudo cp /home/ops/service.yaml.backup /home/ops/service.yaml'
ssh <hostname> 'sudo systemctl restart <service>'
```

## CI/CD Pipeline

SentinelCam uses a 3-stage pipeline:

1. **Bastion (dropsite)**: Receives code packages for deployment
2. **DataSink (integration)**: Package integration, repository updates, create backups
3. **Ramrod (deployment)**: Ansible deployment, production validation

See: [`devops/README.rst`](../../README.rst) for an overview of the design.

## Additional Resources

- **Main Ansible README**: [`devops/ansible/README.md`](../../ansible/README.md)
- **Ansible Beginner Guide**: [`../getting-started/ANSIBLE_BEGINNER_GUIDE.md`](../getting-started/ANSIBLE_BEGINNER_GUIDE.md)
- **Add New Node**: [`../getting-started/ADD_NEW_NODE.md`](../getting-started/ADD_NEW_NODE.md)
- **Multi-Site Deployment**: [`../configuration/MULTI_SITE_DEPLOYMENT.md`](../configuration/MULTI_SITE_DEPLOYMENT.md)
- **Code Deployment Pattern**: [`../configuration/CODE_DEPLOYMENT_PATTERN.md`](../configuration/CODE_DEPLOYMENT_PATTERN.md)

## Role README Cross-Reference

Each role has comprehensive documentation:

| Component | Role README | Playbook |
|-----------|-------------|----------|
| ImageNode (Outposts) | [`roles/imagenode/README.md`](../../ansible/roles/imagenode/README.md) | `deploy-outpost.yaml` |
| CamWatcher (Datasinks) | [`roles/camwatcher/README.md`](../../ansible/roles/camwatcher/README.md) | `deploy-camwatcher.yaml` |
| DataPump (Datasinks) | [`roles/datapump/README.md`](../../ansible/roles/datapump/README.md) | `deploy-datapump.yaml` |
| ImageHub (Datasinks) | [`roles/imagehub/README.md`](../../ansible/roles/imagehub/README.md) | `deploy-imagehub.yaml` |
| Sentinel (AI) | [`roles/sentinel/README.md`](../../ansible/roles/sentinel/README.md) | `deploy-sentinel.yaml` |
| Watchtower (Display) | [`roles/watchtower/README.md`](../../ansible/roles/watchtower/README.md) | `deploy-watchtower.yaml` |
| SentinelCam Base | [`roles/sentinelcam_base/README.md`](../../ansible/roles/sentinelcam_base/README.md) | (included by all) |
| Bastion | [`roles/bastion/README.md`](../../ansible/roles/bastion/README.md) | `deploy-bastion.yaml` |
