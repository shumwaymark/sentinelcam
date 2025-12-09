#!/bin/bash
# Clear SSH known hosts for SentinelCam static IP migration
# This script cleans up SSH host key conflicts after IP migration

echo "=== Clearing SSH Known Hosts for Static IP Migration ==="
echo "Date: $(date)"
echo ""

# Define the static IP addresses that need cleaning
IPS=(
    "192.168.10.10"   # buzz
    "192.168.10.20"   # lab1
    "192.168.10.21"   # east
    "192.168.10.22"   # alpha5
    "192.168.10.50"   # data1
    "192.168.10.60"   # sentinel
    "192.168.10.70"   # wall1
    "192.168.10.254"  # chandler-gate
)

KNOWN_HOSTS_FILE="$HOME/.ssh/known_hosts"

if [[ ! -f "$KNOWN_HOSTS_FILE" ]]; then
    echo "No known_hosts file found at $KNOWN_HOSTS_FILE"
    echo "Nothing to clean"
    exit 0
fi

echo "Backing up current known_hosts file..."
cp "$KNOWN_HOSTS_FILE" "${KNOWN_HOSTS_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
echo "Backup created: ${KNOWN_HOSTS_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
echo ""

echo "Removing old host keys for SentinelCam IPs..."
for ip in "${IPS[@]}"; do
    echo -n "Cleaning $ip: "
    if ssh-keygen -f "$KNOWN_HOSTS_FILE" -R "$ip" >/dev/null 2>&1; then
        echo "✓ Removed"
    else
        echo "- Not found"
    fi
done

echo ""
echo "=== SSH Host Key Cleanup Complete ==="
echo ""
echo "Next steps:"
echo "1. Test SSH connectivity to each node"
echo "2. Accept new host keys when prompted"
echo "3. Run Ansible connectivity tests"
echo ""
echo "You can restore the backup with:"
echo "cp ${KNOWN_HOSTS_FILE}.backup.* $KNOWN_HOSTS_FILE"
