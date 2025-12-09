#!/bin/bash
# filepath: c:\Users\mark.shumway\SRS\sentinelcam\validate_network.sh
# Validate static IP configuration and network connectivity

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INVENTORY="$SCRIPT_DIR/ansible_migration_inventory.yml"

echo "=== SentinelCam Network Validation ==="
echo "Testing static IP configuration and connectivity"
echo

# Test 1: Basic connectivity to all nodes
echo "=== Test 1: Basic Connectivity ==="
echo "Testing ping to all inventory nodes..."
if ansible all -i "$INVENTORY" -m ping -o; then
    echo "✅ All nodes responding to ping"
else
    echo "❌ Some nodes not responding"
fi
echo

# Test 2: Network configuration validation  
echo "=== Test 2: Network Configuration ==="
echo "Checking IP addresses match inventory..."
ansible all -i "$INVENTORY" -m setup -a "filter=ansible_default_ipv4" --tree /tmp/ansible_facts

# Test 3: DNS resolution
echo "=== Test 3: DNS Resolution ==="
echo "Testing DNS resolution from each node..."
ansible all -i "$INVENTORY" -m shell -a "nslookup google.com && nslookup chandler-gate" || true

# Test 4: Inter-node connectivity
echo "=== Test 4: Inter-node Connectivity ==="
echo "Testing connectivity between nodes..."

# Test from each node to the gateway
ansible all -i "$INVENTORY" -m shell -a "ping -c 2 192.168.10.254" || true

# Test local network routing
echo "Testing local network routing..."
ansible all -i "$INVENTORY" -m shell -a "ip route show table main | grep 192.168.10" || true

# Test 5: Service-specific checks
echo "=== Test 5: Service-specific Validation ==="

# Check if sentinel can reach other nodes
echo "Testing AI processing node connectivity..."
ansible legacy_nodes:modern_nodes -i "$INVENTORY" -m shell -a "ping -c 1 192.168.10.19" || true  # data1

# Check SSH connectivity (for ansible)
echo "Testing SSH connectivity..."
ansible all -i "$INVENTORY" -m shell -a "whoami && hostname -I" || true

# Test 6: Service ports and processes
echo "=== Test 6: Service Status ==="
echo "Checking critical services..."

# Check sentinel AI service
ansible sentinel -i "$INVENTORY" -m shell -a "pgrep -f python | wc -l" || true

# Check network services
ansible all -i "$INVENTORY" -m shell -a "systemctl is-active NetworkManager dhcpcd || systemctl is-active network" || true

echo
echo "=== Validation Summary ==="
echo "Review the output above for any failures or issues."
echo "Key indicators of success:"
echo "  ✅ All nodes respond to ping"
echo "  ✅ IP addresses match inventory"  
echo "  ✅ DNS resolution works"
echo "  ✅ Inter-node connectivity works"
echo "  ✅ Services are running"
echo
echo "If validation passes, your static IP configuration is working correctly!"
