#!/bin/bash
# postprocess_pi_sd.sh - Prepare Raspberry Pi OS SD card for headless boot
# This script configures a freshly burned SD card for SentinelCam deployment
#
# Usage: sudo ./postprocess_pi_sd.sh /dev/sdX <hostname> <password>
# Example: sudo ./postprocess_pi_sd.sh /dev/sda north MySecurePass123
#
# What it does:
# - Enables SSH on first boot
# - Creates 'ops' user with specified password
# - Sets hostname (via firstrun.sh)
# - Prepares for headless bootstrap
#
# After booting, run: ansible-playbook playbooks/bootstrap-new-node.yaml

set -e

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: This script must be run as root (use sudo)"
  exit 1
fi

if [ $# -lt 3 ]; then
  echo "Usage: $0 /dev/sdX <hostname> <password>"
  echo "Example: $0 /dev/sda north MySecurePass123"
  exit 1
fi

SD_DEV=$1
HOSTNAME=$2
USERNAME="ops"
PASSWORD=$3

# Validate device exists
if [ ! -b "${SD_DEV}1" ]; then
  echo "ERROR: Boot partition ${SD_DEV}1 not found"
  echo "Available devices:"
  lsblk -o NAME,SIZE,TYPE,MOUNTPOINT | grep -E "disk|part"
  exit 1
fi

BOOT_MNT=/mnt/pi_boot_$$
ROOTFS_MNT=/mnt/pi_rootfs_$$

echo "========================================="
echo "Raspberry Pi SD Card Post-Processor"
echo "========================================="
echo "Device: $SD_DEV"
echo "Hostname: $HOSTNAME"
echo "User: $USERNAME"
echo "Boot partition: ${SD_DEV}1"
echo "Root partition: ${SD_DEV}2"
echo ""

# Create mount points
mkdir -p $BOOT_MNT
mkdir -p $ROOTFS_MNT

# Mount partitions
echo "Mounting partitions..."
mount ${SD_DEV}1 $BOOT_MNT || { echo "ERROR: Failed to mount boot partition"; exit 1; }
mount ${SD_DEV}2 $ROOTFS_MNT || { umount $BOOT_MNT; echo "ERROR: Failed to mount root partition"; exit 1; }

echo "✓ Partitions mounted"

# 1. Enable SSH on first boot
echo "Configuring SSH..."
touch $BOOT_MNT/ssh
echo "✓ SSH enabled"

# 2. Create user credentials (Raspberry Pi OS Bookworm format)
echo "Creating user credentials..."
HASHED=$(echo "$PASSWORD" | openssl passwd -6 -stdin)
echo "$USERNAME:$HASHED" > $BOOT_MNT/userconf
echo "✓ User '$USERNAME' configured"

# 3. Set hostname via /etc/hostname in rootfs
echo "Setting hostname..."
echo "$HOSTNAME" > $ROOTFS_MNT/etc/hostname

# 4. Update /etc/hosts with new hostname
sed -i "s/raspberrypi/$HOSTNAME/g" $ROOTFS_MNT/etc/hosts
echo "✓ Hostname set to '$HOSTNAME'"

# 5. Create firstrun.sh to disable userconf service after first boot
# This prevents re-creation of user on subsequent boots
cat > $BOOT_MNT/firstrun.sh << 'EOF'
#!/bin/bash
# First run script for SentinelCam node
set +e

# Disable the firstrun service so it doesn't run again
systemctl disable firstrun

# Remove this script
rm -f /boot/firstrun.sh
sed -i 's| systemd.run.*||g' /boot/cmdline.txt

exit 0
EOF
chmod +x $BOOT_MNT/firstrun.sh
echo "✓ First-run script created"

# 6. Add firstrun.sh to cmdline.txt so it runs on first boot
if ! grep -q "systemd.run=" $BOOT_MNT/cmdline.txt; then
  sed -i '1 s/$/ systemd.run=\/boot\/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target/' $BOOT_MNT/cmdline.txt
  echo "✓ First-run script enabled in boot configuration"
fi

# Cleanup and unmount
echo ""
echo "Syncing and unmounting..."
sync
umount $ROOTFS_MNT
umount $BOOT_MNT
rmdir $ROOTFS_MNT
rmdir $BOOT_MNT

echo ""
echo "========================================="
echo "✅ SD Card Configuration Complete"
echo "========================================="
echo ""
echo "Configuration Applied:"
echo "  - SSH: Enabled"
echo "  - User: $USERNAME (password set)"
echo "  - Hostname: $HOSTNAME"
echo ""
echo "Next Steps:"
echo "  1. Insert SD card into Raspberry Pi"
echo "  2. Connect Ethernet and power on"
echo "  3. Wait 2-3 minutes for first boot"
echo "  4. Find DHCP address:"
echo "     - Check router DHCP leases"
echo "     - Or: ssh bastion 'sudo grep -i $HOSTNAME /var/lib/dnsmasq/dnsmasq.leases'"
echo "     - Or: nmap -sn 192.168.10.0/24"
echo "  5. Test SSH: ssh $USERNAME@<ip-address>"
echo "  6. Run bootstrap: ansible-playbook -i inventory/bootstrap.yaml playbooks/bootstrap-new-node.yaml"
echo ""
echo "The node will have hostname '$HOSTNAME' after first boot."
echo "========================================="
