#!/bin/bash
# Accept new SSH host keys for SentinelCam nodes after static IP migration

echo "=== Accepting New SSH Host Keys ==="
echo "Date: $(date)"
echo ""

# Define nodes and their details
declare -A NODES=(
    ["lab1"]="192.168.10.20:pi"
    ["east"]="192.168.10.21:ops" 
    ["alpha5"]="192.168.10.22:ops"
    ["data1"]="192.168.10.50:ops"
    ["sentinel"]="192.168.10.60:pi"
    ["wall1"]="192.168.10.70:pi"
)

echo "Testing SSH connectivity and accepting host keys..."
echo ""

for node in "${!NODES[@]}"; do
    IFS=':' read -r ip user <<< "${NODES[$node]}"
    echo -n "Testing $node ($user@$ip): "
    
    # Use ssh-keyscan to get the host key and add it to known_hosts
    if ssh-keyscan -H "$ip" >> ~/.ssh/known_hosts 2>/dev/null; then
        # Test actual SSH connection
        if timeout 10 ssh -o ConnectTimeout=5 -o BatchMode=yes "$user@$ip" "echo 'SSH OK'" >/dev/null 2>&1; then
            echo "✓ Connected"
        else
            echo "✗ SSH failed (but host key accepted)"
        fi
    else
        echo "✗ Host unreachable"
    fi
done

echo ""
echo "=== Host Key Acceptance Complete ==="
echo ""
echo "Now you can run Ansible commands without host key verification issues."
