# SentinelCam Troubleshooting Guide
*Practical Solutions for Common Deployment and Operational Issues*

## 🚦 Quick Diagnostic Commands

### Check Overall System Health

```bash
# From ramrod control node
cd ~/sentinelcam/devops/ansible

# Verify connectivity to all nodes
ansible all -i inventory/production.yaml -m ping

# Check all service statuses
ansible all -i inventory/production.yaml -m shell -a "systemctl status imagenode camwatcher datapump imagehub sentinel watchtower 2>/dev/null | grep -E '(Active|Loaded)'" --become
```

### Check Specific Service Status

```bash
# Direct SSH to node
ssh <node> 'sudo systemctl status <service>'

# Via Ansible
ansible <hostname> -i inventory/production.yaml -m systemd -a "name=<service>" --become
```

---

## 🔴 Service Issues

### Service Won't Start

**Symptoms:** Service fails to start or immediately stops after starting.

**Diagnostic Steps:**

1. **Check service status and logs:**
   ```bash
   ssh <node> 'sudo systemctl status <service>'
   ssh <node> 'sudo journalctl -u <service> -n 100 --no-pager'
   ```

2. **Validate configuration file:**
   ```bash
   # Check YAML syntax
   ssh <node> 'python3 -c "import yaml; yaml.safe_load(open(\"/home/ops/<service>.yaml\"))"'
   
   # Check for legacy pi user paths
   ssh <node> 'cat /home/ops/<service>.yaml | grep -E "(home/pi|home/ops)"'
   ```

3. **Check Python environment:**
   ```bash
   ssh <node> 'ls -la /home/ops/venvs/<service>/'
   ssh <node> '/home/ops/venvs/<service>/bin/python --version'
   ```

4. **Verify code deployment:**
   ```bash
   ssh <node> 'ls -la /home/ops/<service>/'
   ssh <node> 'ls -la /home/ops/<service>/<service>.py'
   ```

**Common Causes & Fixes:**

| Problem | Solution |
|---------|----------|
| Missing Python dependencies | Re-deploy with full provisioning: `ansible-playbook playbooks/deploy-<service>.yaml` |
| Wrong file permissions | `ssh <node> 'sudo chown -R ops:ops /home/ops/<service>/'` |
| Port already in use | Check `inventory/group_vars/all/sentinelcam_ports.yaml`, restart conflicting service |
| Configuration file error | Validate YAML syntax, check variable substitution |
| Missing code files | Re-run `python devops/scripts/sync/deploy.py <service>` from dev workstation |

### Service Running But Not Responding

**Symptoms:** Service status shows "active (running)" but not processing data.

**Diagnostic Steps:**

1. **Check network connectivity:**
   ```bash
   # Test if service port is listening
   ssh <node> 'sudo netstat -tlnp | grep <port>'
   
   # Test connection from another node
   ssh <other_node> 'nc -zv <target_node> <port>'
   ```

2. **Review application logs:**
   ```bash
   # Live tail
   ssh <node> 'sudo journalctl -u <service> -f'
   
   # Search for errors
   ssh <node> 'sudo journalctl -u <service> -n 500 | grep -i error'
   ```

3. **Check resource usage:**
   ```bash
   ssh <node> 'top -b -n 1 | head -20'
   ssh <node> 'df -h'
   ssh <node> 'free -h'
   ```

**Common Causes & Fixes:**

| Problem | Solution |
|---------|----------|
| Wrong hostname/IP in config | Check `inventory/host_vars/<hostname>.yaml` and redeploy with `--tags config` |
| Network isolation | Verify firewall rules, check VPN connection through bastion |
| Hub not reachable | Test imagehub connectivity: `ssh imagenode 'telnet data1 5555'` |
| Disk full | Clean up old data: check datapump retention settings |

---

## 📡 Deployment Issues

### Code Changes Not Taking Effect

**Symptoms:** After deployment, old code still running or changes not visible.

**Diagnostic Steps:**

1. **Verify staging area on data1:**
   ```bash
   ssh data1 'ls -la /home/ops/sentinelcam/current_deployment/<service>/'
   ssh data1 'cat /home/ops/sentinelcam/current_deployment/<service>/<service>.py | head -20'
   ```

2. **Check target node has latest code:**
   ```bash
   ssh <target_node> 'ls -la /home/ops/<service>/'
   ssh <target_node> 'md5sum /home/ops/<service>/<service>.py'
   ssh data1 'md5sum /home/ops/sentinelcam/current_deployment/<service>/<service>.py'
   ```

3. **Verify service restarted:**
   ```bash
   ssh <target_node> 'sudo systemctl status <service> | grep "Active"'
   ssh <target_node> 'ps aux | grep <service>'
   ```

**Resolution:**

```bash
# 1. From development workstation - sync to staging
python devops/scripts/sync/deploy.py <service>

# 2. Verify staging area
ssh data1 'ls -la /home/ops/sentinelcam/current_deployment/<service>/'

# 3. From ramrod - deploy to production
cd ~/sentinelcam/devops/ansible
ansible-playbook playbooks/deploy-<service>.yaml --tags deploy

# 4. Verify and restart
ansible-playbook playbooks/deploy-<service>.yaml --tags deploy --limit <target_node>
ssh <target_node> 'sudo systemctl restart <service>'
```

### Configuration Changes Not Applied

**Symptoms:** Changed `inventory/host_vars` or `inventory/group_vars` but service still using old values.

**Resolution:**

```bash
# Deploy configuration only (no code changes)
ansible-playbook playbooks/deploy-<service>.yaml --tags config

# Or deploy to specific node
ansible-playbook playbooks/deploy-<service>.yaml --tags config --limit <hostname>

# Verify configuration file updated
ssh <hostname> 'cat /home/ops/<service>.yaml'
```

### "Undefined Variable" Errors During Deployment

**Symptoms:** Ansible playbook fails with `variable 'xxx' is undefined`.

**Common Causes:**

1. **Variable not in canonical location:**
   - Ports must be in `inventory/group_vars/all/sentinelcam_ports.yaml`
   - Standards must be in `inventory/group_vars/all/sentinelcam_standards.yaml`
   - Site settings must be in `inventory/group_vars/all/site.yaml`

2. **Typo in variable name:**
   - Check spelling in playbook/role
   - Variables use snake_case: `imagehub_port`, not `imageHubPort`

3. **Missing host-specific variable:**
   - Some services require `inventory/host_vars/<hostname>.yaml`
   - Example: imagenode requires `camera_type`

**Resolution:**

```bash
# Check where variable is defined
grep -r "variable_name" inventory/

# Verify variable in correct file
cat inventory/group_vars/all/sentinelcam_ports.yaml
cat inventory/host_vars/<hostname>.yaml

# Test with verbose mode to see variable values
ansible-playbook playbooks/deploy-<service>.yaml --check -vv --limit <hostname>
```

---

## 🔐 Permission and Access Issues

### "Permission Denied" During Deployment

**Symptoms:** Ansible fails with permission errors.

**Diagnostic Steps:**

1. **Verify correct user in inventory:**
   ```bash
   grep "ansible_user" inventory/production.yaml
   ```

2. **Check SSH access:**
   ```bash
   ssh <node> 'whoami'
   ```

3. **Verify sudo access:**
   ```bash
   ssh <node> 'sudo -l'
   ```

**User Assignment Rules:**

| Node Type | User | Examples |
|-----------|------|----------|
| Modern nodes | `ops` | east, alpha5, data1, ramrod |
| Legacy nodes | `pi` | lab1, sentinel, wall1 |

**Fix incorrect user:**

```bash
# Edit inventory/production.yaml or inventory/group_vars/legacy_nodes.yaml
ansible_user: ops  # or pi for legacy nodes

# Test connection
ansible <hostname> -i inventory/production.yaml -m ping
```

### SSH Connection Failures

**Symptoms:** Cannot connect to nodes, connection timeout.

**Resolution:**

```bash
# 1. Verify VPN connection (if remote)
ssh bastion 'wg show'

# 2. Test direct SSH
ssh -v <target_node>

# 3. Check node is reachable
ping <target_node>

# 4. Verify SSH key
ssh-add -l

# 5. Test through bastion
ssh -J bastion <target_node>
```

---

## 📷 Camera and Hardware Issues

### Camera Not Detected (ImageNode)

**Symptoms:** ImageNode starts but no camera frames captured.

**Diagnostic Steps:**

1. **Check camera hardware:**
   ```bash
   ssh <imagenode> 'vcgencmd get_camera'  # RPi Camera
   ssh <imagenode> 'ls -la /dev/video*'   # USB Camera
   ssh <imagenode> 'v4l2-ctl --list-devices'  # USB Camera details
   ```

2. **Check camera configuration:**
   ```bash
   ssh <imagenode> 'cat /home/ops/imagenode.yaml | grep -A 5 camera'
   ```

3. **Review imagenode logs:**
   ```bash
   ssh <imagenode> 'sudo journalctl -u imagenode -n 100 | grep -i camera'
   ```

**Common Fixes:**

| Problem | Solution |
|---------|----------|
| Wrong camera_type | Edit `inventory/host_vars/<hostname>.yaml`, set `camera_type: PiCamera` or `camera_type: USB` |
| Camera not enabled | RPi: `sudo raspi-config` → Interface → Enable Camera |
| USB camera permissions | `sudo usermod -aG video ops`, then reboot |
| Multiple cameras | Specify device: `camera_device: /dev/video0` in host_vars |

### NCS2 / Coral Accelerator Not Detected

**Symptoms:** Sentinel service fails to initialize AI accelerator.

**Diagnostic Steps:**

1. **Check device presence:**
   ```bash
   ssh sentinel 'lsusb | grep -E "(Movidius|Google)"'
   ```

2. **Check OpenVINO/EdgeTPU runtime:**
   ```bash
   ssh sentinel '/home/ops/venvs/sentinel/bin/python -c "import openvino"'
   ssh sentinel '/home/ops/venvs/sentinel/bin/python -c "from pycoral.utils import edgetpu"'
   ```

3. **Check udev rules:**
   ```bash
   ssh sentinel 'ls -la /etc/udev/rules.d/*movidius* /etc/udev/rules.d/*coral*'
   ```

**Resolution:**

```bash
# Re-run full sentinel provisioning
ansible-playbook playbooks/deploy-sentinel.yaml --limit sentinel

# Verify accelerator libraries installed
ssh sentinel 'pip list | grep -E "(openvino|pycoral)"'
```

---

## 🎯 Data Flow Issues

### Images Not Appearing in Camwatcher/Datapump

**Symptoms:** ImageNode running, but no images stored on datasink.

**Diagnostic Chain:**

```bash
# 1. Verify ImageNode is sending
ssh <imagenode> 'sudo journalctl -u imagenode -n 50 | grep -i "send\|pub"'

# 2. Verify ImageHub is receiving
ssh data1 'sudo journalctl -u imagehub -n 50 | grep -i "receiv"'

# 3. Verify Camwatcher is processing
ssh data1 'sudo journalctl -u camwatcher -n 50 | grep -i "image\|event"'

# 4. Check ZMQ connectivity
ssh <imagenode> 'telnet data1 5555'
```

**Common Causes:**

1. **ImageHub port misconfigured:** Check `sentinelcam_ports.yaml`
2. **Wrong hub address in imagenode config:** Check `inventory/host_vars/<imagenode>.yaml`
3. **Network isolation:** Verify routing through bastion
4. **Disk full on datasink:** `ssh data1 'df -h'`

---

## 🛡️ Safe Practice and Rollback

### Before Making Changes

```bash
# 1. Test with check mode (no changes)
ansible-playbook playbooks/deploy-<service>.yaml --check --diff

# 2. Limit to single node first
ansible-playbook playbooks/deploy-<service>.yaml --limit <test_node>

# 3. Monitor logs during deployment
ssh <test_node> 'sudo journalctl -u <service> -f'
```

### If Something Goes Wrong

```bash
# 1. Stop the service immediately
ssh <node> 'sudo systemctl stop <service>'

# 2. Review what changed
ssh <node> 'sudo journalctl -u <service> -n 200 --no-pager'

# 3. Restore previous configuration (manual)
ssh <node> 'sudo cp /home/ops/<service>.yaml.backup /home/ops/<service>.yaml'

# 4. Restart with old config
ssh <node> 'sudo systemctl start <service>'

# 5. Re-deploy known good version from development workstation
python devops/scripts/sync/deploy.py <service>
cd ~/sentinelcam/devops/ansible
ansible-playbook playbooks/deploy-<service>.yaml --tags deploy --limit <node>
```

---

## 📞 Emergency Reference

### Critical Service Recovery

```bash
# Stop all services on a node
ssh <node> 'sudo systemctl stop imagenode camwatcher datapump imagehub sentinel watchtower'

# Check what's consuming resources
ssh <node> 'top -b -n 1 | head -20'
ssh <node> 'sudo systemctl --failed'

# Restart individual service
ssh <node> 'sudo systemctl restart <service>'

# Full reboot (last resort)
ssh <node> 'sudo reboot'
```

### Quick Health Check

```bash
# From ramrod control node
cd ~/sentinelcam/devops/ansible

# Check all nodes reachable
ansible all -i inventory/production.yaml -m ping

# Check all service statuses
ansible outposts -i inventory/production.yaml -m shell -a "systemctl is-active imagenode" --become
ansible datasinks -i inventory/production.yaml -m shell -a "systemctl is-active camwatcher imagehub datapump" --become
ansible sentinels -i inventory/production.yaml -m shell -a "systemctl is-active sentinel" --become
ansible watchtowers -i inventory/production.yaml -m shell -a "systemctl is-active watchtower" --become
```

---

## 📚 Getting More Help

### View Detailed Role Documentation

- [Base Provisioning](../../ansible/roles/sentinelcam_base/README.md)
- [ImageNode (Camera)](../../ansible/roles/imagenode/README.md)
- [ImageHub (Aggregation)](../../ansible/roles/imagehub/README.md)
- [Camwatcher (Event Log)](../../ansible/roles/camwatcher/README.md)
- [Datapump (Storage)](../../ansible/roles/datapump/README.md)
- [Sentinel (AI/ML)](../../ansible/roles/sentinel/README.md)
- [Watchtower (Display)](../../ansible/roles/watchtower/README.md)

### Additional Resources

- [Ansible Beginner's Guide](../getting-started/ANSIBLE_BEGINNER_GUIDE.md) - Learn Ansible safely
- [Code Deployment Pattern](../configuration/CODE_DEPLOYMENT_PATTERN.md) - How code flows through the system
- [Outpost Registry Pattern](../configuration/OUTPOST_REGISTRY_PATTERN.md) - How nodes are configured
