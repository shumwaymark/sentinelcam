#!/bin/bash
# Add a new node to SentinelCam production environment
#
# This script automates the complete workflow for adding a new Raspberry Pi node
# to your SentinelCam deployment. It guides you through configuration, bootstrapping,
# network setup, and service deployment.
#
# Usage:
#   ./add-new-node.sh <node_name> <temp_ip> <static_ip> <role> [type] [interface]
#
# Arguments:
#   node_name    - Hostname for the new node (e.g., "north", "south", "lab2")
#   temp_ip      - Temporary DHCP IP address assigned to the new node
#   static_ip    - Permanent static IP address to assign to the node
#   role         - Node role: outpost, datasink, ai_processing, watchtower
#   type         - Node type: modern (default) or legacy
#   interface    - Network interface name (default: eth0)
#
# Examples:
#   ./add-new-node.sh north 192.168.10.105 192.168.10.23 outpost
#   ./add-new-node.sh lab2 192.168.10.106 192.168.10.24 outpost legacy
#   ./add-new-node.sh data2 192.168.10.51 192.168.10.51 datasink modern eth0
#
# Options:
#   --skip-bootstrap    Skip the bootstrap phase
#   --skip-network      Skip the network configuration phase
#   --whatif            Show what would be done without executing
#   --help              Show this help message
#
# Prerequisites:
#   - Ansible installed and configured
#   - SSH access to the new node
#   - devops/ansible directory structure present
#
# See devops/docs/ADD_NEW_NODE.md for complete documentation.
#
# Author: Mark Shumway
# Date: November 26, 2025
# Version: 1.0

set -e  # Exit on error

# ANSI color codes
COLOR_RESET='\033[0m'
COLOR_GREEN='\033[0;32m'
COLOR_YELLOW='\033[1;33m'
COLOR_BLUE='\033[0;34m'
COLOR_RED='\033[0;31m'
COLOR_CYAN='\033[0;36m'
COLOR_BOLD='\033[1m'

# Configuration
ANSIBLE_DIR="devops/ansible"
BOOTSTRAP_INVENTORY="$ANSIBLE_DIR/inventory/bootstrap.yaml"
PRODUCTION_INVENTORY="$ANSIBLE_DIR/inventory/production.yaml"

# Flags
SKIP_BOOTSTRAP=false
SKIP_NETWORK=false
WHATIF=false

# Functions
print_phase() {
    echo -e "\n${COLOR_CYAN}${COLOR_BOLD}=== $1 ===${COLOR_RESET}\n"
}

print_success() {
    echo -e "${COLOR_GREEN}✓${COLOR_RESET} $1"
}

print_warning() {
    echo -e "${COLOR_YELLOW}⚠${COLOR_RESET} $1"
}

print_info() {
    echo -e "${COLOR_BLUE}ℹ${COLOR_RESET} $1"
}

print_error() {
    echo -e "${COLOR_RED}✗${COLOR_RESET} $1" >&2
}

show_help() {
    sed -n '2,40p' "$0" | sed 's/^# \?//'
    exit 0
}

validate_ip() {
    local ip=$1
    if [[ ! $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        print_error "Invalid IP address: $ip"
        return 1
    fi
    return 0
}

validate_role() {
    local role=$1
    case $role in
        outpost|datasink|ai_processing|watchtower)
            return 0
            ;;
        *)
            print_error "Invalid role: $role"
            print_info "Valid roles: outpost, datasink, ai_processing, watchtower"
            return 1
            ;;
    esac
}

test_prerequisites() {
    print_phase "Checking Prerequisites"
    
    # Check Ansible
    if ! command -v ansible &> /dev/null; then
        print_error "Ansible not found. Please install Ansible first."
        return 1
    fi
    print_success "Ansible found: $(ansible --version | head -n1)"
    
    # Check directory structure
    if [[ ! -d "$ANSIBLE_DIR" ]]; then
        print_error "Ansible directory not found: $ANSIBLE_DIR"
        print_info "Please run this script from the sentinelcam root directory"
        return 1
    fi
    print_success "Ansible directory structure found"
    
    # Check inventory files
    if [[ ! -f "$BOOTSTRAP_INVENTORY" ]]; then
        print_error "Bootstrap inventory not found: $BOOTSTRAP_INVENTORY"
        return 1
    fi
    print_success "Bootstrap inventory found"
    
    if [[ ! -f "$PRODUCTION_INVENTORY" ]]; then
        print_warning "Production inventory not found: $PRODUCTION_INVENTORY"
        print_info "This is OK for first-time setup"
    else
        print_success "Production inventory found"
    fi
    
    return 0
}

test_connectivity() {
    local ip=$1
    local user=$2
    
    print_info "Testing connectivity to ${user}@${ip}..."
    
    if ansible all -i "${ip}," -u "$user" -m ping &> /dev/null; then
        print_success "SSH connectivity confirmed"
        return 0
    else
        print_error "Cannot connect to ${user}@${ip}"
        return 1
    fi
}

update_bootstrap_inventory() {
    local node_name=$1
    local temp_ip=$2
    local static_ip=$3
    local role=$4
    local type=$5
    local interface=$6
    local user=$7
    
    print_phase "Updating Bootstrap Inventory"
    
    # Backup existing inventory
    local backup_path="${BOOTSTRAP_INVENTORY}.backup.$(date +%Y%m%d_%H%M%S)"
    cp "$BOOTSTRAP_INVENTORY" "$backup_path"
    print_info "Backup created: $backup_path"
    
    # Create new_node configuration
    local new_node_config="        new_node:
          ansible_host: $temp_ip
          ansible_user: $user
          target_hostname: $node_name
          target_ip: $static_ip
          target_role: $role
          target_type: $type
          interface: $interface"
    
    # Use sed to replace the new_node section
    # This is a simplified approach - adjust the pattern based on your inventory structure
    sed -i.tmp "/new_node:/,/interface:/ c\\
$new_node_config" "$BOOTSTRAP_INVENTORY"
    rm -f "${BOOTSTRAP_INVENTORY}.tmp"
    
    print_success "Bootstrap inventory updated"
    
    # Display configuration
    echo -e "\nConfiguration:"
    echo "  Hostname: $node_name"
    echo "  Temp IP: $temp_ip"
    echo "  Static IP: $static_ip"
    echo "  Role: $role"
    echo "  Type: $type"
    echo "  User: $user"
    echo "  Interface: $interface"
}

run_bootstrap() {
    local node_name=$1
    local static_ip=$2
    
    print_phase "Phase 1: Bootstrap Node"
    
    print_info "This will configure system settings, users, packages, and SSH keys"
    print_info "Duration: ~10-15 minutes (includes system package updates)"
    
    if [[ "$WHATIF" == "true" ]]; then
        print_warning "WhatIf mode: Would run bootstrap playbook"
        return 0
    fi
    
    echo -e "\nRunning bootstrap playbook...\n"
    
    local cmd="ansible-playbook -i $BOOTSTRAP_INVENTORY ${ANSIBLE_DIR}/playbooks/bootstrap-new-node.yaml --extra-vars \"target_hostname=$node_name target_ip=$static_ip\""
    
    echo "$cmd"
    if eval "$cmd"; then
        print_success "Bootstrap completed successfully"
        return 0
    else
        print_error "Bootstrap failed with exit code $?"
        return 1
    fi
}

run_network_configuration() {
    local node_name=$1
    local static_ip=$2
    
    print_phase "Phase 2: Configure Static IP"
    
    print_warning "Network service will restart - SSH connection will drop briefly"
    print_info "After configuration, the node will be accessible at: $static_ip"
    
    if [[ "$WHATIF" == "true" ]]; then
        print_warning "WhatIf mode: Would run network configuration playbook"
        return 0
    fi
    
    echo -e "\nRunning network configuration playbook...\n"
    
    local cmd="ansible-playbook -i $BOOTSTRAP_INVENTORY ${ANSIBLE_DIR}/playbooks/configure-static-network.yaml --extra-vars \"target_hostname=$node_name target_ip=$static_ip\""
    
    echo "$cmd"
    if eval "$cmd"; then
        print_success "Network configuration completed"
        
        print_info "Waiting 30 seconds for network to stabilize..."
        sleep 30
        
        # Test new IP
        print_info "Testing connectivity to static IP: $static_ip"
        if ping -c 2 "$static_ip" &> /dev/null; then
            print_success "Node responding on static IP: $static_ip"
            return 0
        else
            print_warning "Node not yet responding on $static_ip"
            print_info "You may need to wait longer or check network configuration"
            return 1
        fi
    else
        print_error "Network configuration failed with exit code $?"
        return 1
    fi
}

add_to_production_inventory() {
    local node_name=$1
    local static_ip=$2
    local role=$3
    local type=$4
    local interface=$5
    local user=$6
    
    print_phase "Phase 3: Add to Production Inventory"
    
    print_info "Please manually add the following to $PRODUCTION_INVENTORY"
    
    local node_group
    [[ "$type" == "modern" ]] && node_group="modern_nodes" || node_group="legacy_nodes"
    
    local functional_group
    case $role in
        outpost) functional_group="outposts" ;;
        datasink) functional_group="datasinks" ;;
        ai_processing) functional_group="ai_processing" ;;
        watchtower) functional_group="watchtowers" ;;
    esac
    
    echo -e "\n${COLOR_YELLOW}# Add to $node_group -> hosts:${COLOR_RESET}"
    cat << EOF
            $node_name:
              ansible_host: $static_ip
              ansible_user: $user
              node_name: $node_name
              node_role: $role
              interface: $interface
EOF
    
    echo -e "\n${COLOR_YELLOW}# Add to $functional_group -> hosts:${COLOR_RESET}"
    echo "        $node_name:"
    
    echo ""
    
    read -p "Press Enter when you have updated the production inventory..."
    
    # Verify production inventory connection
    print_info "Testing connection via production inventory..."
    if ansible -i "$PRODUCTION_INVENTORY" "$node_name" -m ping &> /dev/null; then
        print_success "Node accessible via production inventory"
        return 0
    else
        print_error "Cannot reach node via production inventory"
        print_info "Please verify the inventory configuration"
        return 1
    fi
}

run_service_deployment() {
    local node_name=$1
    local role=$2
    
    print_phase "Phase 4: Deploy Services"
    
    local playbook
    case $role in
        outpost)
            playbook="${ANSIBLE_DIR}/playbooks/deploy-outpost-complete.yaml"
            ;;
        datasink)
            print_warning "Datasink requires multiple playbooks"
            print_info "Please run these manually:"
            echo "  ansible-playbook -i $PRODUCTION_INVENTORY ${ANSIBLE_DIR}/playbooks/deploy-camwatcher.yaml --limit $node_name"
            echo "  ansible-playbook -i $PRODUCTION_INVENTORY ${ANSIBLE_DIR}/playbooks/deploy-datapump.yaml --limit $node_name"
            echo "  ansible-playbook -i $PRODUCTION_INVENTORY ${ANSIBLE_DIR}/playbooks/deploy-imagehub.yaml --limit $node_name"
            return 0
            ;;
        ai_processing)
            playbook="${ANSIBLE_DIR}/playbooks/deploy-sentinel.yaml"
            ;;
        watchtower)
            playbook="${ANSIBLE_DIR}/playbooks/deploy-watchtower.yaml"
            ;;
    esac
    
    if [[ ! -f "$playbook" ]]; then
        print_error "Playbook not found: $playbook"
        return 1
    fi
    
    print_info "Deploying $role services to $node_name"
    print_info "This will install code, dependencies, and start services"
    
    if [[ "$WHATIF" == "true" ]]; then
        print_warning "WhatIf mode: Would run deployment playbook"
        return 0
    fi
    
    echo -e "\nRunning deployment playbook...\n"
    
    local cmd="ansible-playbook -i $PRODUCTION_INVENTORY $playbook --limit $node_name"
    echo "$cmd"
    if eval "$cmd"; then
        print_success "Service deployment completed"
        return 0
    else
        print_error "Service deployment failed with exit code $?"
        return 1
    fi
}

verify_services() {
    local node_name=$1
    local role=$2
    local user=$3
    local static_ip=$4
    
    print_phase "Phase 5: Verify Services"
    
    local service
    case $role in
        outpost) service="imagenode" ;;
        ai_processing) service="sentinel" ;;
        watchtower) service="watchtower" ;;
        datasink)
            print_info "Datasink services: camwatcher, datapump, imagehub"
            return 0
            ;;
    esac
    
    if [[ "$WHATIF" == "true" ]]; then
        print_warning "WhatIf mode: Would check service status"
        return 0
    fi
    
    print_info "Checking service status for: $service"
    
    ansible -i "$PRODUCTION_INVENTORY" "$node_name" -m shell -a "systemctl status $service" --become
    
    echo -e "\nTo view live logs:"
    echo "  ssh ${user}@${static_ip}"
    echo "  journalctl -u $service -f"
}

# Parse command line options
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-bootstrap)
            SKIP_BOOTSTRAP=true
            shift
            ;;
        --skip-network)
            SKIP_NETWORK=true
            shift
            ;;
        --whatif)
            WHATIF=true
            shift
            ;;
        --help|-h)
            show_help
            ;;
        *)
            break
            ;;
    esac
done

# Parse positional arguments
if [[ $# -lt 4 ]]; then
    print_error "Insufficient arguments"
    echo ""
    show_help
fi

NODE_NAME=$1
TEMP_IP=$2
STATIC_IP=$3
ROLE=$4
TYPE=${5:-modern}
INTERFACE=${6:-eth0}

# Validate inputs
if ! validate_ip "$TEMP_IP"; then
    exit 1
fi

if ! validate_ip "$STATIC_IP"; then
    exit 1
fi

if ! validate_role "$ROLE"; then
    exit 1
fi

if [[ "$TYPE" != "modern" && "$TYPE" != "legacy" ]]; then
    print_error "Invalid type: $TYPE (must be 'modern' or 'legacy')"
    exit 1
fi

# Determine user based on type
if [[ "$TYPE" == "modern" ]]; then
    USER="ops"
else
    USER="pi"
fi

# Main execution
print_phase "SentinelCam New Node Setup"
echo "Node: $NODE_NAME"
echo "Role: $ROLE"
echo "Type: $TYPE"

# Check prerequisites
if ! test_prerequisites; then
    exit 1
fi

# Test initial connectivity
if ! test_connectivity "$TEMP_IP" "$USER"; then
    print_error "Cannot establish initial connection to node"
    print_info "Please verify:"
    print_info "  1. Node is powered on and network cable connected"
    print_info "  2. DHCP IP address is correct: $TEMP_IP"
    print_info "  3. SSH is enabled on the node"
    print_info "  4. User '$USER' exists with correct password"
    exit 1
fi

# Update bootstrap inventory
update_bootstrap_inventory "$NODE_NAME" "$TEMP_IP" "$STATIC_IP" "$ROLE" "$TYPE" "$INTERFACE" "$USER"

# Phase 1: Bootstrap
if [[ "$SKIP_BOOTSTRAP" == "false" ]]; then
    if ! run_bootstrap "$NODE_NAME" "$STATIC_IP"; then
        exit 1
    fi
else
    print_warning "Skipping bootstrap phase"
fi

# Phase 2: Network Configuration
if [[ "$SKIP_NETWORK" == "false" ]]; then
    if ! run_network_configuration "$NODE_NAME" "$STATIC_IP"; then
        print_warning "Network configuration may need manual intervention"
    fi
else
    print_warning "Skipping network configuration phase"
fi

# Phase 3: Production Inventory
if ! add_to_production_inventory "$NODE_NAME" "$STATIC_IP" "$ROLE" "$TYPE" "$INTERFACE" "$USER"; then
    print_warning "Production inventory setup incomplete"
fi

# Phase 4: Service Deployment
if ! run_service_deployment "$NODE_NAME" "$ROLE"; then
    print_warning "Service deployment incomplete"
fi

# Phase 5: Verification
verify_services "$NODE_NAME" "$ROLE" "$USER" "$STATIC_IP"

# Success summary
print_phase "Setup Complete!"
print_success "Node $NODE_NAME is configured and operational"
echo ""
echo "Summary:"
echo "  Hostname: $NODE_NAME"
echo "  IP Address: $STATIC_IP"
echo "  Role: $ROLE"
echo "  Type: $TYPE"
echo ""
echo "Next Steps:"
echo "  1. Verify services are running: ssh ${USER}@${STATIC_IP}"
echo "  2. Check logs: journalctl -u <service> -f"
echo "  3. Update documentation in devops/docs/"
echo "  4. Add to monitoring systems if applicable"
echo ""
echo "For detailed documentation, see: devops/docs/ADD_NEW_NODE.md"
