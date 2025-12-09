# Node Onboarding Guide

## Overview

This guide covers the complete process for adding a new node to the SentinelCam infrastructure, including network configuration, Ansible inventory setup, and enabling code deployment.

## Prerequisites

- [ ] New Raspberry Pi hardware prepared
- [ ] Network connectivity established
- [ ] SSH access configured from control node (buzz)
- [ ] Determine node type: sentinel, outpost, watchtower, or datasink

**Note:** All new nodes use the `ops` user. The `pi` user exists only on legacy nodes during OS migration.

## Step 1: Base System Setup

### 1.1 Flash Raspberry Pi OS

Use Raspberry Pi Imager to flash SD card with:
- Raspberry Pi OS Lite (Debian-based)
- Configure WiFi (if applicable)
- Enable SSH
- Set hostname: `<node_name>.local`

### 1.2 Initial SSH Connection

```bash
# From control node (buzz)
ssh pi@<node_name>.local
```

### 1.3 Create ops User

**All new nodes use the `ops` user as standard.**

```bash
# On the new node as pi user
sudo useradd -m -s /bin/bash ops
sudo usermod -aG sudo ops
echo "ops ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/ops

# Set password
sudo passwd ops

# Copy SSH authorized_keys
sudo mkdir -p /home/ops/.ssh
sudo cp ~/.ssh/authorized_keys /home/ops/.ssh/
sudo chown -R ops:ops /home/ops/.ssh
sudo chmod 700 /home/ops/.ssh
sudo chmod 600 /home/ops/.ssh/authorized_keys

# Test ops user SSH
exit
ssh ops@<node_name>.local
```

## Step 2: Network Configuration

### 2.1 Determine IP Address

Assign static IP based on node type:
- Sentinels: 192.168.10.60-79
- Outposts: 192.168.10.100-199
- Watchtowers: 192.168.10.40-59
- Data sinks: 192.168.10.80-89

### 2.2 Configure Static IP

See `configuration/NETWORK_CONFIGURATION.md` for details on setting up static IP addressing.

## Step 3: Ansible Inventory Setup

### 3.1 Add to production.yaml

Edit `devops/ansible/inventory/production.yaml`:

```yaml
# Add to appropriate group
sentinels:
  hosts:
    new_sentinel_name:
      ansible_host: 192.168.10.XX

# Or for outposts
outposts:
  hosts:
    new_outpost_name:
      ansible_host: 192.168.10.XX
      outpost_id: "new_site_id"  # Unique identifier
```

### 3.2 Classify Node Type

Add to modern_nodes group:

```yaml
# All new nodes go in modern_nodes (ops user)
modern_nodes:
  children:
    - new_sentinel_name

# legacy_nodes is for migration only - do not add new nodes here
```

### 3.3 Set ansible_user

The ansible_user is automatically set to `ops` for all modern_nodes.

**Do not override unless absolutely necessary.** All new deployments should use `ops` user:

```yaml
# ansible_user: ops is inherited from modern_nodes group
# No need to specify explicitly
new_sentinel_name:
  ansible_host: 192.168.10.XX
```

### 3.4 Create host_vars (If Needed)

For node-specific configuration, create:
`devops/ansible/inventory/host_vars/<node_name>.yaml`

```yaml
---
# Node-specific overrides
sentinel_model_type: "mobilenet_v2"  # Example
camera_resolution: "1920x1080"
```

## Step 4: Verify Ansible Connectivity

### 4.1 Test Connection

```bash
# From control node in devops/ansible directory
ansible new_node_name -m ping
```

Expected output:
```
new_node_name | SUCCESS => {
    "changed": false,
    "ping": "pong"
}
```

### 4.2 Verify Variables

```bash
ansible-inventory --host new_node_name --yaml
```

Check that:
- `ansible_host` is correct IP
- `ansible_user` is `ops` (all new nodes)
- `sentinelcam_user` is `ops` (all new nodes)
- Node is in `modern_nodes` group

## Step 5: SSH Key Distribution

### 5.1 Setup Code Deployment Keys

Run SSH key distribution playbook:

```bash
ansible-playbook playbooks/setup-ssh-keys.yaml --limit new_node_name
```

This will:
1. Ensure data1 has SSH key pair generated
2. Copy data1's public key to new node's authorized_keys
3. Verify SSH connectivity from data1 to new node

### 5.2 Manual Verification

```bash
# SSH to data1 first
ssh ops@data1

# From data1, test connection to new node
ssh <user>@<new_node_ip> 'echo Success'
```

If successful, code deployment will work.

## Step 6: Provision Node

### 6.1 Run Base Provisioning

```bash
# Full provisioning (installs packages, configs, code)
ansible-playbook playbooks/deploy-<type>.yaml --limit new_node_name
```

Where `<type>` is:
- `sentinel` for AI processing nodes
- `outpost` for remote camera nodes
- `watchtower` for consolidated nodes
- `datasink` for data collection nodes

### 6.2 Verify Provisioning

```bash
# Check for errors in playbook output
# Verify services started:
ansible new_node_name -m shell -a "systemctl status imagenode" -b
```

## Step 7: Deploy Code

### 7.1 Initial Code Deployment

```bash
# Deploy just the code (assumes packages already installed)
ansible-playbook playbooks/deploy-<type>.yaml --limit new_node_name --tags deploy
```

### 7.2 Verify Deployment

```bash
# SSH to new node
ssh <user>@<new_node_name>

# Check code exists
ls -la ~/<component_name>/

# Check service running
sudo systemctl status <component_name>
```

## Step 8: Validate Configuration

### 8.1 Run Validation Playbook

```bash
ansible-playbook playbooks/validate-configuration.yaml --limit new_node_name
```

### 8.2 Check for Common Issues

- User consistency (sentinelcam_user matches actual user)
- Directory permissions
- Service status
- Network connectivity to hub/data sink

## Step 9: Commit Changes

### 9.1 Review Changes

```bash
git status
git diff devops/ansible/inventory/
```

### 9.2 Commit to Repository

```bash
git add devops/ansible/inventory/production.yaml
git add devops/ansible/inventory/host_vars/<node_name>.yaml  # If created
git commit -m "Add <node_name> to inventory"
git push
```

## Step 10: Document Node

### 10.1 Update Node Registry

Add entry to site documentation:

```yaml
# In appropriate site docs
nodes:
  - name: new_node_name
    type: sentinel|outpost|watchtower|datasink
    ip: 192.168.10.XX
    user: ops  # All new nodes use ops
    purpose: "Brief description of node's role"
    deployment_date: YYYY-MM-DD
```

### 10.2 Update Network Diagram

If maintaining network documentation, update diagrams to include new node.

## Troubleshooting Common Issues

### Cannot Ping Node

**Issue:** `ansible new_node -m ping` fails

**Solutions:**
1. Verify SSH connectivity: `ssh <user>@<ip>`
2. Check inventory: `ansible-inventory --host new_node --yaml`
3. Verify ansible_user matches actual user on node
4. Check SSH keys in authorized_keys

### Code Deployment Fails

**Issue:** `--tags deploy` fails with "Permission denied"

**Solutions:**
1. Verify SSH key setup: `ansible-playbook playbooks/setup-ssh-keys.yaml --limit new_node`
2. Test from data1: `ssh ops@data1`, then `ssh <user>@<new_node>`
3. Check authorized_keys permissions: `ls -la ~/.ssh/` on target node

### Variables Not Defined

**Issue:** Playbook shows "VARIABLE IS NOT DEFINED"

**Solutions:**
1. Check group membership: `ansible-inventory --host new_node --yaml`
2. Verify group_vars location: `ls devops/ansible/inventory/group_vars/`
3. Check variable inheritance: `ansible new_node -m debug -a "var=<var_name>"`

### Service Won't Start

**Issue:** Systemd service fails to start after deployment

**Solutions:**
1. Check service status: `sudo systemctl status <service>`
2. View logs: `sudo journalctl -u <service> -n 50`
3. Verify code permissions: `ls -la ~/<component>/`
4. Check configuration file: `cat ~/<component>/*.yaml`

## Onboarding Checklist Template

Use this checklist when adding a new node:

```
Node Name: __________________
Node Type: [ ] Sentinel [ ] Outpost [ ] Watchtower [ ] Datasink
User Type: [X] Modern (ops) - Standard for all new nodes
IP Address: 192.168.10._____

[ ] Base system flashed and SSH accessible
[ ] Ops user created and configured
[ ] Static IP configured
[ ] Added to inventory/production.yaml
[ ] Added to modern_nodes group (standard)
[ ] host_vars created (if needed)
[ ] Ansible ping successful
[ ] ansible-inventory shows correct variables
[ ] SSH keys distributed (setup-ssh-keys.yaml)
[ ] SSH verified from data1 to new node
[ ] Base provisioning completed
[ ] Code deployment successful
[ ] Services running
[ ] Validation playbook passed
[ ] Changes committed to git
[ ] Documentation updated
```

## Quick Reference Commands

```bash
# From control node (buzz)
cd ~/sentinelcam/devops/ansible

# Test connectivity
ansible new_node -m ping

# View variables
ansible-inventory --host new_node --yaml

# Setup SSH keys
ansible-playbook playbooks/setup-ssh-keys.yaml --limit new_node

# Provision node
ansible-playbook playbooks/deploy-<type>.yaml --limit new_node

# Deploy code only
ansible-playbook playbooks/deploy-<type>.yaml --limit new_node --tags deploy

# Validate configuration
ansible-playbook playbooks/validate-configuration.yaml --limit new_node
```

## See Also

- `SSH_KEY_DISTRIBUTION_STRATEGY.md` - SSH key infrastructure details
- `configuration/NETWORK_CONFIGURATION.md` - Static IP setup
- `configuration/DEPLOYMENT_OVERVIEW.md` - Deployment architecture
- `configuration/CODE_DEPLOYMENT_PATTERN.md` - How code deployment works
- `ansible/README.md` - Ansible infrastructure overview
