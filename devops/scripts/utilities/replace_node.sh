#!/bin/bash
# Complete node replacement workflow for SentinelCam
# Usage: ./replace_node.sh <node_name> <temp_dhcp_ip> <final_static_ip>

set -e

# Validate arguments
if [ $# -ne 3 ]; then
    echo "Usage: $0 <node_name> <temp_dhcp_ip> <final_static_ip>"
    echo "Example: $0 lab1 192.168.10.100 192.168.10.20"
    exit 1
fi

NODE_NAME=$1
TEMP_IP=$2
FINAL_IP=$3
ANSIBLE_DIR="~/sentinelcam/devops/ansible"

echo "=== SentinelCam Node Replacement Workflow ==="
echo "Node: $NODE_NAME"
echo "Temporary IP: $TEMP_IP"
echo "Final Static IP: $FINAL_IP"
echo

# Step 1: Update bootstrap inventory with temporary IP
echo "Step 1: Updating bootstrap inventory..."
sed -i "s/ansible_host: .*/ansible_host: $TEMP_IP/" $ANSIBLE_DIR/inventory/bootstrap.yaml
sed -i "s/target_hostname: .*/target_hostname: $NODE_NAME/" $ANSIBLE_DIR/inventory/bootstrap.yaml
sed -i "s/target_ip: .*/target_ip: $FINAL_IP/" $ANSIBLE_DIR/inventory/bootstrap.yaml

# Step 2: Test connectivity
echo "Step 2: Testing SSH connectivity..."
ansible -i $ANSIBLE_DIR/inventory/bootstrap.yaml new_nodes -m ping

# Step 3: Bootstrap the node
echo "Step 3: Bootstrapping new node..."
ansible-playbook -i $ANSIBLE_DIR/inventory/bootstrap.yaml \
    $ANSIBLE_DIR/playbooks/bootstrap-new-node.yaml \
    --extra-vars "target_hostname=$NODE_NAME target_ip=$FINAL_IP"

# Step 4: Configure static IP
echo "Step 4: Configuring static IP..."
ansible-playbook -i $ANSIBLE_DIR/inventory/bootstrap.yaml \
    $ANSIBLE_DIR/playbooks/configure_static_ips_playbook.yaml \
    --extra-vars "target_ip=$FINAL_IP"

# Wait for network restart
echo "Waiting for network restart..."
sleep 30

# Step 5: Test connectivity on new IP
echo "Step 5: Testing connectivity on static IP..."
ansible -i $ANSIBLE_DIR/inventory/production.yaml $NODE_NAME -m ping

# Step 6: Deploy services
echo "Step 6: Deploying services..."
ansible-playbook -i $ANSIBLE_DIR/inventory/production.yaml \
    $ANSIBLE_DIR/playbooks/deploy.yaml --limit $NODE_NAME

echo
echo "=== Node replacement completed successfully! ==="
echo "Node $NODE_NAME is now running at $FINAL_IP with all services deployed."
echo
echo "Next steps:"
echo "1. Disconnect old node from network"
echo "2. Connect new node to production network"
echo "3. Verify services are running: systemctl status imagenode"
echo "4. Check logs: journalctl -u imagenode -f"
