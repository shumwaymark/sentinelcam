# Adding a New Node to SentinelCam

## Overview

This guide walks you through adding a new Raspberry Pi node to your SentinelCam deployment. The process takes a fresh Raspberry Pi OS installation from initial boot to fully operational node with the correct role and configuration.

## Prerequisites

### Hardware
- Raspberry Pi (4B or newer recommended)
- MicroSD card (32GB+ recommended)
- Network connection (Ethernet preferred)
- Camera module (for outpost nodes)

### Software
- **Raspberry Pi Imager** (for creating bootable SD card)
- **SSH access** from your Windows control machine
- **Ansible** installed and configured on `buzz` (control node)

### Before You Start
- Choose a **hostname** for the new node
- Determine the **node role** (outpost, datasink, ai_processing, watchtower)
- Assign a **static IP address** from the network plan
- Verify the IP is not already in use

## Network Addressing Plan

Refer to `devops/docs/NETWORK_ADDRESSING_STANDARD.md` for IP allocation strategy.

**Current Production Nodes:**
```
192.168.10.10    - buzz (control node)
192.168.10.20    - lab1 (outpost)
192.168.10.21    - east (outpost)
192.168.10.22    - alpha5 (outpost)
192.168.10.50    - data1 (datasink)
192.168.10.60    - sentinel (ai_processing)
192.168.10.70    - wall1 (watchtower)
192.168.10.254   - chandler-gate (bastion)
```

**Available IP Ranges:**
- Outposts: 192.168.10.23-192.168.10.39
- Datasinks: 192.168.10.51-192.168.10.59
- AI Processing: 192.168.10.61-192.168.10.69
- Watchtowers: 192.168.10.71-192.168.10.79

---

## Phase 1: Prepare the SD Card

### 1. Write Pre-Downloaded Image to SD Card (Headless Method)

1. Download the latest Raspberry Pi OS Lite (64-bit) image (Bookworm or newer) and store it on the primary data sink (e.g., `/home/ops/sentinelcam/diskimages/raspios/`).
2. Insert the target microSD card into your Linux workstation or the data sink.
3. Write the image to the SD card using:
   ```bash
   xzcat /home/ops/sentinelcam/diskimages/raspios/2024-11-19-raspios-bookworm-arm64-lite.img.xz | sudo dd of=/dev/sdX status=progress bs=4M
   sync
   ```
   Replace `/dev/sdX` with your SD card device (double-check with `lsblk`).

### 2. Post-Process SD Card for Headless Boot

After imaging, run the provided post-processing script to enable headless setup (no keyboard/monitor required):

```bash
sudo ./devops/scripts/utilities/postprocess_pi_sd.sh /dev/sdX <hostname> <password>

# Example:
sudo ./devops/scripts/utilities/postprocess_pi_sd.sh /dev/sda north MySecurePass123
```

**What this does:**
- ✅ Enables SSH on first boot
- ✅ Creates `ops` user with specified password
- ✅ Sets hostname to your chosen name
- ✅ Configures first-run script for clean initialization

**Note:** The script requires root access and `openssl` for password hashing.

**Important:** Remove the SD card safely:
```bash
sudo eject /dev/sdX
```

3. Insert the SD card into the Raspberry Pi and power it on.
4. Continue with finding the node's DHCP address below.

---

## Phase 2: Initial Boot and Connection

### 1. Boot the New Node

1. Insert SD card into Raspberry Pi
2. Connect Ethernet cable to your network
3. Power on the Raspberry Pi
4. Wait 2-3 minutes for first boot to complete

### 2. Find the Node's DHCP Address

From chandler-gate (bastion):

```bash
# Check dnsmasq DHCP leases (most reliable)
ssh rocky@chandler-gate.local
sudo cat /var/lib/misc/dnsmasq.leases | grep north

# Or scan the network
sudo nmap -sn 192.168.10.0/24 | grep -B 2 "Raspberry"
```

From Windows control machine:

```powershell
# Scan your network for the new node
arp -a | Select-String "192.168.10"
```

Or check your router's DHCP lease table for the newest device with hostname "north".

### 3. Test Initial SSH Connection

```powershell
ssh ops@192.168.10.XXX    # Use the DHCP IP you found
# Or
ssh ops@<node-name>.local

# Accept the SSH key fingerprint when prompted
```

**First Time Login Checklist:**
- [ ] SSH connection successful
- [ ] Can sudo without password issues: `sudo apt update`
- [ ] Network connectivity working: `ping 192.168.10.254`

---

## Phase 3: Define Node in Ansible Inventory

### 1. Decide Node Type and Role

**Node Types:**
- **modern_nodes**: Raspberry Pi 4/5 running Bookworm+ (uses systemd-networkd)
- **legacy_nodes**: Older Raspberry Pi running Bullseye or earlier (uses dhcpcd)

**Node Roles:**
- **outpost**: Camera node running imagenode service
- **datasink**: Data storage running camwatcher, datapump, imagehub
- **ai_processing**: AI inference node running sentinel service
- **watchtower**: Display/monitoring node running watchtower service

### 2. Update Bootstrap Inventory

Edit `devops/ansible/inventory/bootstrap.yaml`:

```yaml
all:
  children:
    new_nodes:
      hosts:
        new_node:
          ansible_host: 192.168.10.XXX      # Current DHCP IP
          ansible_user: ops
          target_hostname: <node-name>       # Final hostname
          target_ip: 192.168.10.YYY          # Final static IP
          target_role: outpost               # Node role
          interface: eth0
```

**Example for a new outpost node:**
```yaml
        new_node:
          ansible_host: 192.168.10.105       # Temporary DHCP IP
          ansible_user: ops
          target_hostname: north
          target_ip: 192.168.10.23           # Assigned static IP
          target_role: outpost
          interface: eth0
```

### 3. Test Ansible Connectivity

From `buzz` or your control machine:

```bash
cd ~/sentinelcam/devops/ansible

# Test connection to the new node (will prompt for password)
ansible -i inventory/bootstrap.yaml new_nodes -m ping -k

# Enter the password you set in postprocess_pi_sd.sh when prompted
# Should see GREEN success message
```

**Important**: Use the `-k` flag to prompt for SSH password. This is required because the controller's SSH key hasn't been installed yet.

---

## Phase 4: Bootstrap the Node

This phase provisions the base system: users, groups, timezone, locale, packages, and SSH keys.

### 1. Review Bootstrap Playbook

The bootstrap playbook (`playbooks/bootstrap_new_node.yaml`) will:
- Set hostname and timezone
- Update system packages
- Install essential utilities (git, vim, htop, etc.)
- Configure `ops` user with proper groups (gpio, spi, i2c, video)
- Set up SSH keys for passwordless access
- Configure sudo access

### 2. Run Bootstrap Playbook

**Important**: Have the password ready that you set during SD card post-processing.

```bash
cd ~/sentinelcam/devops/ansible

# Run bootstrap playbook with password authentication
ansible-playbook -i inventory/bootstrap.yaml \
  playbooks/bootstrap_new_node.yaml \
  --extra-vars "target_hostname=north target_ip=192.168.10.23" \
  -k

# When prompted, enter the password from postprocess_pi_sd.sh
# The playbook will install the controller's SSH key for future passwordless access
```

**What to Expect:**
- Password prompt at start (SSH password: )
- Task output showing each configuration step
- Package updates (may take 5-10 minutes)
- Controller's SSH key installed - no password needed after this
- Success message at completion

### 3. Verify Bootstrap

```bash
# Test connection using temporary DHCP IP (no password needed now - key-based auth)
ansible -i inventory/bootstrap.yaml new_nodes -m shell -a "hostname"
# Should return: north

ansible -i inventory/bootstrap.yaml new_nodes -m shell -a "date"
# Should show correct timezone
```

---

## Phase 5: Configure Static IP

This phase assigns the permanent static IP address and configures network management.

### 1. Run Network Configuration Playbook

```bash
ansible-playbook -i inventory/bootstrap.yaml \
  playbooks/configure_static_network.yaml \
  --extra-vars "target_hostname=north target_ip=192.168.10.23"
```

**The playbook will:**
- Detect network manager type (systemd-networkd or dhcpcd)
- Backup existing network configuration
- Deploy static IP configuration
- Restart networking service

### 2. Wait for Network Restart

**IMPORTANT**: The node will reboot to ensure clean network configuration.

Wait for the playbook to complete (it will wait for the node to come back online), then verify:

```bash
# Test new static IP
ping 192.168.10.23

# Test SSH on new IP
ssh ops@192.168.10.23

# Verify only one IP is active
ssh ops@192.168.10.23 'ip -4 addr show eth0 | grep inet'
# Should show ONLY the static IP, no secondary addresses
```

### 3. Clear Bastion DHCP Leases

**CRITICAL**: The bastion's dnsmasq may have cached the old DHCP address. Clear it:

```bash
# From chandler-gate
ssh rocky@chandler-gate.local
sudo rm -f /var/lib/misc/dnsmasq.leases
sudo systemctl restart dnsmasq

# Verify DNS now returns correct IP
dig @localhost north.local +short
# Should return: 192.168.10.23 (the static IP, not old DHCP)

# Test name resolution
ping north.local
```

Without this step, DNS will return the wrong IP!

---

## Phase 6: Add Node to Production Inventory

Now that the node has its permanent IP, add it to the production inventory.

### 1. Edit Production Inventory

Edit `devops/ansible/inventory/production.yaml`:

**For a modern outpost node:**
```yaml
    sentinelcam_nodes:
      children:
        modern_nodes:
          hosts:
            # ... existing nodes ...
            north:
              ansible_host: 192.168.10.23
              ansible_user: ops
              node_name: north
              node_role: outpost
              interface: eth0
```

**Add to functional group:**
```yaml
    outposts:
      hosts:
        # ... existing outposts ...
        north:
```

### 2. Verify Production Inventory

```bash
# Test connection via production inventory
ansible -i inventory/production.yaml north -m ping

# Verify group membership
ansible -i inventory/production.yaml outposts -m shell -a "hostname"
# Should list all outposts including north
```

---

## Phase 7: Deploy Node Role and Services

Deploy the appropriate services based on the node's role.

### 1. Run Role-Specific Playbook

**For Outpost Nodes (imagenode):**
```bash
ansible-playbook -i inventory/production.yaml \
  playbooks/deploy-outpost-complete.yaml \
  --limit north
```

**For Datasink Nodes (camwatcher, datapump, imagehub):**
```bash
ansible-playbook -i inventory/production.yaml \
  playbooks/deploy-camwatcher.yaml \
  --limit <node-name>

ansible-playbook -i inventory/production.yaml \
  playbooks/deploy-datapump.yaml \
  --limit <node-name>

ansible-playbook -i inventory/production.yaml \
  playbooks/deploy-imagehub.yaml \
  --limit <node-name>
```

**For AI Processing Nodes (sentinel):**
```bash
ansible-playbook -i inventory/production.yaml \
  playbooks/deploy_sentinel.yaml \
  --limit <node-name>
```

**For Watchtower Nodes:**
```bash
ansible-playbook -i inventory/production.yaml \
  playbooks/deploy_watchtower.yaml \
  --limit <node-name>
```

### 2. What Gets Deployed

The deployment playbook will:
- Run `sentinelcam_base` role (user, venv, directories)
- Install Python dependencies
- Deploy service-specific code
- Create service configuration files
- Install and enable systemd services
- Start the services

---

## Phase 8: Verify and Test

### 1. Check Service Status

```bash
# SSH to the new node
ssh ops@192.168.10.23

# Check service status
sudo systemctl status imagenode
# Should show: active (running)

# Check service logs
journalctl -u imagenode -f
# Watch for successful startup messages
```

### 2. Verify Network Connectivity

**For outpost nodes:**
- Verify connection to imagehub: Check logs for ZeroMQ connection
- Test camera: Ensure camera is detected and streaming

**For datasink nodes:**
- Verify receiving images from outposts
- Check database connections
- Verify file storage paths

### 3. Integration Testing

From the control machine:

```bash
# Check node is responding in its functional group
ansible -i inventory/production.yaml outposts -m shell -a "systemctl is-active imagenode"

# Verify Python environment
ansible -i inventory/production.yaml north -m shell \
  -a "/home/ops/.opencv/bin/python -c 'import cv2; print(cv2.__version__)'"
```

---

## Phase 9: Document and Finalize

### 1. Update Documentation

Update these files to reflect the new node:

- `devops/docs/NETWORK_ADDRESSING_STANDARD.md` - Add IP assignment
- `README.rst` or system documentation - Add node to topology
- Any monitoring or alerting configurations

### 2. Add to Monitoring

If you have monitoring systems (Nagios, Prometheus, etc.):
- Add node to monitoring configuration
- Set up health checks
- Configure alerts

### 3. Create Node-Specific Configuration

If the node needs custom configuration:

Create `devops/ansible/inventory/node_configs/<node-name>.yaml`:
```yaml
---
# Node-specific configuration for north
camera_resolution: "(1280, 720)"
camera_framerate: 24
detection_objects: mobilenetssd
accelerator: ncs2
```

### 4. Backup Checklist

- [ ] Node added to inventory (bootstrap and production)
- [ ] Static IP documented
- [ ] Services deployed and running
- [ ] Configuration files created
- [ ] Monitoring configured
- [ ] Documentation updated

---

## Troubleshooting

### Node Not Reachable After Static IP Configuration

**Problem**: Can't ping or SSH to the new static IP.

**Solutions:**
1. Check if network service restarted: `sudo systemctl status systemd-networkd`
2. Verify IP configuration: `ip addr show eth0`
3. Check routing: `ip route`
4. Verify gateway is reachable: `ping 192.168.10.254`
5. Reboot if necessary: `sudo reboot`

### Service Failed to Start

**Problem**: `systemctl status <service>` shows failed state.

**Solutions:**
1. Check service logs: `journalctl -u <service> -n 100`
2. Verify configuration file: `cat ~/<service>.yaml`
3. Check Python environment: `~/.opencv/bin/python -c 'import <module>'`
4. Verify user/group permissions: `ls -la ~/<service>/`
5. Test service manually: `~/.opencv/bin/python ~/<service>/<service>/<service>.py`

### Bootstrap Playbook Hangs or Fails

**Problem**: Ansible playbook doesn't complete.

**Solutions:**
1. Verify SSH connectivity: `ansible -i inventory/bootstrap.yaml new_nodes -m ping`
2. Check if node can reach internet for package updates: `ping 8.8.8.8`
3. Verify sudo access: `ssh ops@<ip> sudo whoami`
4. Check disk space: `df -h`
5. Review playbook output for specific error messages

### Wrong Network Manager Type

**Problem**: Static IP configuration fails due to wrong network manager.

**Solutions:**
1. Check which network manager is active:
   ```bash
   systemctl is-active systemd-networkd  # Modern
   systemctl is-active dhcpcd            # Legacy
   ```
2. Update `target_type` in bootstrap inventory
3. Rerun network configuration playbook

### Camera Not Detected

**Problem**: Camera not working on outpost node.

**Solutions:**
1. Enable camera interface: `sudo raspi-config` → Interface Options → Camera
2. Check camera connection: `libcamera-hello --list-cameras`
3. Verify user in video group: `groups ops`
4. Check picamera2 installation: `python -c 'import picamera2'`
5. Reboot after enabling camera

---

## Quick Reference Commands

```bash
# Test connectivity
ansible -i inventory/bootstrap.yaml new_nodes -m ping
ansible -i inventory/production.yaml <node-name> -m ping

# Bootstrap new node
ansible-playbook -i inventory/bootstrap.yaml \
  playbooks/bootstrap_new_node.yaml \
  --extra-vars "target_hostname=<name> target_ip=<ip>"

# Configure static IP
ansible-playbook -i inventory/bootstrap.yaml \
  playbooks/configure_static_network.yaml \
  --extra-vars "target_hostname=<name> target_ip=<ip>"

# Deploy outpost
ansible-playbook -i inventory/production.yaml \
  playbooks/deploy-outpost-complete.yaml \
  --limit <node-name>

# Check service status
ansible -i inventory/production.yaml <node-name> \
  -m shell -a "systemctl status imagenode"

# Restart service
ansible -i inventory/production.yaml <node-name> \
  -m systemd -a "name=imagenode state=restarted" --become

# View service logs
ssh ops@<node-ip> journalctl -u imagenode -f
```

---

## Next Steps After Adding a Node

1. **Test End-to-End Functionality**
   - For outposts: Verify image streaming to datasink
   - For datasinks: Verify data processing and storage
   - For AI nodes: Test inference pipeline

2. **Performance Tuning**
   - Adjust camera resolution/framerate if needed
   - Configure detection thresholds
   - Optimize network buffer sizes

3. **Integration**
   - Update dashboard configurations
   - Add to alerting systems
   - Configure backup routines

4. **Documentation**
   - Add to network diagram
   - Document any custom configurations
   - Update runbooks

---

## Summary

**The complete workflow:**

1. ✅ Create bootable SD card with Raspberry Pi Imager
2. ✅ Boot node and get DHCP IP
3. ✅ Add node to `bootstrap.yaml` inventory
4. ✅ Run `bootstrap_new_node.yaml` playbook
5. ✅ Run `configure_static_network.yaml` playbook
6. ✅ Add node to `production.yaml` inventory
7. ✅ Deploy role-specific services
8. ✅ Verify services are running
9. ✅ Update documentation

**Expected Timeline:**
- SD card preparation: 10 minutes
- Initial boot and connection: 5 minutes
- Bootstrap playbook: 10-15 minutes
- Network configuration: 2-3 minutes
- Service deployment: 5-10 minutes
- **Total: ~40-50 minutes per node**

---

## Related Documentation

- `devops/docs/NETWORK_ADDRESSING_STANDARD.md` - Network planning
- `devops/ansible/roles/sentinelcam_base/README.md` - Base role details
- `devops/ansible/roles/imagenode/README.md` - Outpost node details
- `copilot_work/COMPLETE_ANSIBLE_INFRASTRUCTURE_SOLUTION.md` - Infrastructure overview

---

**Last Updated**: November 26, 2025  
**Author**: Mark Shumway  
**Version**: 1.0
