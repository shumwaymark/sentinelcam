# Jetson Nano Setup Guide

## Current State

**Node:** deepend (Jetson Nano ML training node)  
**Current IP:** 192.168.10.205 (DHCP)  
**Target IP:** 192.168.10.100 (static, per network addressing plan)  
**User:** pyimagesearch (PyImageSearch tutorial image)  
**OS:** Ubuntu 18.04.6 LTS (Bionic Beaver)  
**SSH:** Password authentication (needs key-based)  
**Sudo:** Requires password (needs passwordless)  
**Ansible Controller:** buzz (192.168.10.10)  

## Setup Tasks

### Prerequisites (Run from buzz - Ansible controller)

```bash
# SSH to the Ansible controller node
ssh ops@buzz  # or ssh ops@192.168.10.10

# Navigate to ansible directory
cd ~/sentinelcam/devops/ansible

# Test connection to Jetson at its current DHCP address
# Note: Jetson is currently at 192.168.10.205 (DHCP)
# Will be changed to 192.168.10.100 (static) by provisioning playbook
ansible ml_trainers -m ping -e "ansible_host=192.168.10.205" --ask-pass

# If ping fails, verify:
# 1. Jetson is on network: ping 192.168.10.205
# 2. SSH is enabled: ssh pyimagesearch@192.168.10.205
# 3. Inventory will use .100 but we override for initial connection
```

### Automated Provisioning

```bash
# Run provisioning playbook (will prompt for password initially)
# Override ansible_host temporarily since Jetson is still on DHCP
ansible-playbook playbooks/provision-jetson-nano.yaml \
  -e "ansible_host=192.168.10.205" \
  --ask-pass \
  --ask-become-pass

# This will:
# ✅ Deploy SSH keys for passwordless access
# ✅ Configure passwordless sudo
# ✅ Set static IP (192.168.10.205 → 192.168.10.100)
# ✅ Hostname already correct (deepend)
# ✅ Install essential packages
# ✅ Optimize Jetson power settings
# ✅ Verify CUDA availability
```

### Reboot After Provisioning

```bash
# Reboot to apply hostname change
ansible ml_trainers -b -a "reboot"

# Wait 2 minutes...

# Verify connectivity (no password needed now!)
ansible ml_trainers -m ping

# Verify hostname changed
ansible ml_trainers -a "hostname"
```

### Deploy ML Training Role

```bash
# Deploy deepthink role
ansible-playbook playbooks/deploy-deepthink.yaml

# Verify installation
ansible ml_trainers -a "ls -la /home/pyimagesearch/deepthink"
ansible ml_trainers -a "/home/pyimagesearch/.virtualenvs/ml-training/bin/python --version"
```

## Manual Steps (If Automation Fails)

### 1. SSH Key Distribution

```bash
# From workstation
ssh-copy-id pyimagesearch@192.168.10.80
```

### 2. Passwordless Sudo

```bash
ssh pyimagesearch@192.168.10.80
### 3. Static IP Configuration

```bash
# On Jetson (if manual configuration needed)
sudo nano /etc/netplan/01-netcfg.yaml
```

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    eth0:
      dhcp4: no
      addresses:
        - 192.168.10.100/24
      gateway4: 192.168.10.254
      nameservers:
        addresses: [192.168.10.254, 8.8.8.8]
```     - 192.168.10.80/24
      gateway4: 192.168.10.254
      nameservers:
        addresses: [192.168.10.254, 8.8.8.8]
```

```bash
sudo netplan apply
```

### 4. Hostname Configuration

```bash
# Verify hostname is correct
hostname
# Should already be: deepend (no change needed)

# If it needs to be set:
# sudo hostnamectl set-hostname deepend
```

### 5. System Updates

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv python3-dev build-essential git rsync
```

## Jetson-Specific Configuration

### Power Mode

```bash
# Check current mode
sudo nvpmodel -q

# Set to MAXN (10W, all cores enabled)
sudo nvpmodel -m 0

# Enable all CPU cores
for i in 0 1 2 3; do
  echo 1 | sudo tee /sys/devices/system/cpu/cpu${i}/online
done
```

### CUDA Verification

```bash
# Check CUDA version
nvcc --version

# Check JetPack components
dpkg -l | grep nvidia
```

### Swap Configuration (Optional, for memory-intensive training)

```bash
# Check current swap
free -h

# If swap is too small, increase (use file-based swap, not zram)
# Note: Don't put swap on SD card, use NVMe if available
```

## Network Configuration

### Update DNS Resolution

```bash
# Add to /etc/hosts on Jetson
echo "192.168.10.50 data1" | sudo tee -a /etc/hosts
echo "192.168.10.60 sentinel" | sudo tee -a /etc/hosts
echo "192.168.10.254 chandler-gate" | sudo tee -a /etc/hosts
```

### Test Connectivity to Other Nodes

```bash
ping -c 3 data1
ping -c 3 sentinel
ping -c 3 chandler-gate
```

## Verification Checklist

After provisioning is complete, verify:

```bash
# From workstation:
cd devops/ansible

# ✅ Passwordless SSH
ansible ml_trainers -m ping

# ✅ Passwordless sudo
ansible ml_trainers -b -a "whoami"  # Should return "root"

# ✅ Static IP persists after reboot
ansible ml_trainers -a "ip addr show eth0"

# ✅ Hostname changed
ansible ml_trainers -a "hostname"  # Should return "deepthink"

# ✅ Python available
ansible ml_trainers -a "python3 --version"

# ✅ CUDA available
ansible ml_trainers -a "nvcc --version"

# ✅ Essential packages installed
ansible ml_trainers -a "which git rsync pip3"
```

## Troubleshooting

### Can't connect after IP change

If static IP configuration breaks connectivity:

```bash
# Connect via serial console or HDMI
# Revert netplan config
sudo cp /etc/netplan/01-netcfg.yaml.backup /etc/netplan/01-netcfg.yaml
sudo netplan apply

# Or manually configure IP
sudo ip addr add 192.168.10.80/24 dev eth0
sudo ip route add default via 192.168.10.254
```

### Hostname change doesn't persist

```bash
# Ensure both locations are updated
sudo hostnamectl set-hostname deepthink
echo "127.0.1.1 deepthink" | sudo tee -a /etc/hosts
sudo reboot
```

### CUDA not found

```bash
# Add CUDA to PATH
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc
```

## Post-Setup: ML Training Deployment

Once provisioning is complete, proceed to ML training setup:

```bash
# Deploy ML training infrastructure
ansible-playbook playbooks/deploy-deepthink.yaml

# Test training script
ssh pyimagesearch@deepthink
/home/pyimagesearch/deepthink/train_face_model.sh --help
```

## Notes

- **PyImageSearch Image**: The Jetson came pre-configured from PyImageSearch tutorial
- **Username**: Keep `pyimagesearch` to avoid breaking JetPack Python installations
- **JetPack**: Pre-installed TensorFlow, PyTorch rely on specific paths/users
- **Power**: Jetson Nano runs at 5-10W, safe to leave on 24/7
- **SD Card**: Consider NVMe SSD for better I/O during training
- **Cooling**: Add fan for sustained training workloads

## Security Notes

Since this is an internal network device:
- ✅ SSH key-based auth (no password exposure)
- ✅ Passwordless sudo acceptable (embedded training node)
- ✅ Static IP prevents DHCP conflicts
- ✅ No external network exposure (behind bastion)
