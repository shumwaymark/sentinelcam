#!/bin/bash
# deployment-health-monitor.sh - Monitor the health and status of code transmission pipeline
# Can be run from any pipeline stage to check overall system health

set -eE  # Exit on error, enable error trapping

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEALTH_LOG="/tmp/sentinelcam_deployment_health_$(date +%Y%m%d).log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Health check results
HEALTH_STATUS="HEALTHY"
HEALTH_WARNINGS=0
HEALTH_ERRORS=0

# Logging function
log() {
    echo -e "[$TIMESTAMP] $1" | tee -a "$HEALTH_LOG"
}

# Health check function
check_health() {
    local component="$1"
    local check_cmd="$2"
    local expected="$3"
    
    log "${BLUE}Checking $component...${NC}"
    
    if eval "$check_cmd" > /dev/null 2>&1; then
        if [ "$expected" = "success" ]; then
            log "${GREEN}[+] $component: HEALTHY${NC}"
            return 0
        else
            log "${YELLOW}[!] $component: WARNING - unexpected success${NC}"
            ((HEALTH_WARNINGS++))
            return 1
        fi
    else
        if [ "$expected" = "failure" ]; then
            log "${GREEN}[+] $component: HEALTHY (expected failure)${NC}"
            return 0
        else
            log "${RED}[!] $component: ERROR${NC}"
            ((HEALTH_ERRORS++))
            HEALTH_STATUS="UNHEALTHY"
            return 1
        fi
    fi
}

# Check pipeline stage status
check_pipeline_status() {
    log "${BLUE}--- Checking Pipeline Stage Status ---${NC}"
    
    # Determine current host and check appropriate status
    HOSTNAME=$(hostname)
    
    case "$HOSTNAME" in
        *bastion*|*rocky*)
            log "Detected BASTION host: $HOSTNAME"
            check_bastion_health
            ;;
        *data1*|*ops*)
            log "Detected DATA1 host: $HOSTNAME"
            check_data1_health
            ;;
        *)
            # Detect control node (ramrod) by presence of ansible home
            if [ -d "~/sentinelcam/devops/ansible/inventory" ]; then
                log "Detected CONTROL NODE (ramrod) host: $HOSTNAME"
                check_control_node_health
            else
                log "${YELLOW}Unknown host type: $HOSTNAME - running generic checks${NC}"
                check_generic_health
            fi
            ;;
    esac
}

# Bastion-specific health checks
check_bastion_health() {
    # Check cache directory
    check_health "Bastion Cache Directory" "test -d ~/transfer_cache" "success"
    
    # Check SSH connectivity to data1
    check_health "SSH to Data1" "ssh -o ConnectTimeout=5 ops@data1 'echo test'" "success"
    
    # Check recent upload processing
    if [ -f "~/transfer_cache/last_deployment.status" ]; then
        last_deployment=$(cat ~/transfer_cache/last_deployment.status)
        log "${GREEN}[+] Last deployment: $last_deployment${NC}"
    else
        log "${YELLOW}[!] No recent deployment status found${NC}"
        ((HEALTH_WARNINGS++))
    fi
    
    # Check test script availability
    check_health "Test Framework Available" "test -f $SCRIPT_DIR/run-tests.sh" "success"
}

# Data1-specific health checks  
check_data1_health() {
    # Check source directory
    check_health "Source Directory" "test -d ~/sentinelcam" "success"
    
    # Check devops directory
    check_health "DevOps Directory" "test -d ~/sentinelcam/devops" "success"
    
    # Check SSH connectivity to control node
    # Get control node hostname from ansible inventory if available
    if [ -f "~/sentinelcam/devops/ansible/inventory/production.yaml" ]; then
        CONTROL_NODE=$(grep -A 5 'control:' ~/sentinelcam/devops/ansible/inventory/production.yaml 2>/dev/null | grep 'ansible_host:' | head -1 | awk '{print $2}' || echo "")
    fi
    if [ -n "$CONTROL_NODE" ]; then
        check_health "SSH to Control Node" "ssh -o ConnectTimeout=5 pi@$CONTROL_NODE 'echo test'" "success"
    fi
    
    # Check test status
    if [ -f "~/sentinelcam/staging/.deploy_flags/test_status" ]; then
        test_status=$(tail -1 ~/sentinelcam/staging/.deploy_flags/test_status)
        if echo "$test_status" | grep -q "TESTS_PASSED"; then
            log "${GREEN}[+] Latest tests: PASSED${NC}"
        elif echo "$test_status" | grep -q "TESTS_FAILED"; then
            log "${RED}[!] Latest tests: FAILED${NC}"
            ((HEALTH_ERRORS++))
            HEALTH_STATUS="UNHEALTHY"
        else
            log "${YELLOW}[!] Test status unclear: $test_status${NC}"
            ((HEALTH_WARNINGS++))
        fi
    else
        log "${YELLOW}[!] No test status available${NC}"
        ((HEALTH_WARNINGS++))
    fi
    
    # Check deployment flags
    if [ -d "~/sentinelcam/staging/.deploy_flags" ]; then
        flag_count=$(find ~/sentinelcam/staging/.deploy_flags -name "*" -type f | wc -l)
        log "${GREEN}[+] Deployment flags: $flag_count active${NC}"
        
        # Check for stale deployment flags (older than 24 hours)
        check_stale_flags "~/sentinelcam/staging/.deploy_flags"
    fi
    
    # Check staging area for stuck deployments
    check_staging_health "~/sentinelcam/staging/incoming"
}

# Control node (ramrod) specific health checks
check_control_node_health() {
    # Check ansible home
    check_health "Ansible Home" "test -d ~/sentinelcam/devops/ansible" "success"
    
    # Check ansible connectivity
    if [ -f "~/sentinelcam/devops/ansible/inventory/production.yaml" ]; then
        check_health "Ansible Connectivity" "ansible all -i ~/sentinelcam/devops/ansible/inventory/production.yaml -m ping --timeout=10" "success"
    else
        log "${YELLOW}[!] Ansible inventory not found${NC}"
        ((HEALTH_WARNINGS++))
    fi
    
    # Check production validation script
    check_health "Production Validation Script" "test -x $SCRIPT_DIR/production-validation.sh" "success"
    
    # Check last deployment status
    if [ -f "~/sentinelcam/devops/ansible/logs/last_deployment.status" ]; then
        last_deployment=$(cat ~/sentinelcam/devops/ansible/logs/last_deployment.status)
        log "${GREEN}[+] Last deployment: $last_deployment${NC}"
    else
        log "${YELLOW}[!] No deployment status found${NC}"
        ((HEALTH_WARNINGS++))
    fi
    
    # Check deployment history
    check_deployment_history
}

# Generic health checks for unknown hosts
check_generic_health() {
    # Check basic system health
    check_health "Disk Space" "test $(df / | tail -1 | awk '{print $5}' | sed 's/%//') -lt 90" "success"
    check_health "Load Average" "test $(uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | sed 's/,//') -lt 5" "success"
    check_health "Memory Usage" "test $(free | grep Mem | awk '{print ($3/$2) * 100.0}' | cut -d. -f1) -lt 90" "success"
}

# Check for stale deployment flags
check_stale_flags() {
    local flags_dir="$1"
    
    if [ ! -d "$flags_dir" ]; then
        return 0
    fi
    
    log "${BLUE}Checking for stale deployment flags...${NC}"
    
    local stale_count=0
    local now=$(date +%s)
    
    while IFS= read -r flag_file; do
        if [ -f "$flag_file" ]; then
            local file_age=$(stat -c %Y "$flag_file" 2>/dev/null || stat -f %m "$flag_file" 2>/dev/null)
            local age_hours=$(( (now - file_age) / 3600 ))
            
            if [ "$age_hours" -gt 24 ]; then
                local flag_name=$(basename "$flag_file")
                log "${YELLOW}[!] Stale flag: $flag_name (${age_hours}h old)${NC}"
                ((stale_count++))
                ((HEALTH_WARNINGS++))
            fi
        fi
    done < <(find "$flags_dir" -type f 2>/dev/null)
    
    if [ "$stale_count" -eq 0 ]; then
        log "${GREEN}[+] No stale flags detected${NC}"
    else
        log "${YELLOW}[!] Found $stale_count stale deployment flags${NC}"
    fi
}

# Check staging area health
check_staging_health() {
    local staging_dir="$1"
    
    if [ ! -d "$staging_dir" ]; then
        return 0
    fi
    
    log "${BLUE}Checking staging area health...${NC}"
    
    # Check for stuck files in staging (older than 1 hour)
    local stuck_files=$(find "$staging_dir" -type f -mmin +60 2>/dev/null | wc -l)
    
    if [ "$stuck_files" -gt 0 ]; then
        log "${YELLOW}[!] Found $stuck_files files in staging older than 1 hour${NC}"
        log "${YELLOW}    This may indicate a stuck deployment${NC}"
        ((HEALTH_WARNINGS++))
    else
        log "${GREEN}[+] Staging area is clean${NC}"
    fi
}

# Check deployment history across pipeline
check_deployment_history() {
    log "${BLUE}--- Checking Deployment History ---${NC}"
    
    HOSTNAME=$(hostname)
    
    case "$HOSTNAME" in
        *bastion*|*rocky*)
            # Check bastion transfer cache logs
            if [ -d "~/transfer_cache/logs" ]; then
                recent_transfers=$(find ~/transfer_cache/logs -name "*.log" -mtime -1 2>/dev/null | wc -l)
                log "${GREEN}[+] Recent transfers (24h): $recent_transfers${NC}"
            fi
            ;;
        *data1*|*ops*)
            # Check data1 integration logs
            if [ -d "~/sentinelcam/devops/scripts/sync/logs" ]; then
                recent_integrations=$(find ~/sentinelcam/devops/scripts/sync/logs -name "integration*.log" -mtime -1 2>/dev/null | wc -l)
                log "${GREEN}[+] Recent integrations (24h): $recent_integrations${NC}"
            fi
            
            # Check ansible sync status
            if [ -f "~/sentinelcam/devops/ansible/.last_sync" ]; then
                last_sync=$(cat ~/sentinelcam/devops/ansible/.last_sync)
                log "${GREEN}[+] Last ansible sync: $last_sync${NC}"
            fi
            ;;
        *)
            # Control node (ramrod) - check ansible deployment logs
            if [ -d "~/sentinelcam/devops/ansible/logs" ]; then
                recent_deployments=$(find ~/sentinelcam/devops/ansible/logs -name "deployment*.log" -mtime -1 2>/dev/null | wc -l)
                log "${GREEN}[+] Recent deployments (24h): $recent_deployments${NC}"
                
                # Show last deployment details
                latest_log=$(ls -t ~/sentinelcam/devops/ansible/logs/deployment*.log 2>/dev/null | head -1)
                if [ -n "$latest_log" ]; then
                    log "${BLUE}Latest deployment log: $(basename "$latest_log")${NC}"
                fi
            fi
            ;;
    esac
}

# Check system resources
check_system_resources() {
    log "${BLUE}--- Checking System Resources ---${NC}"
    
    # Disk space
    disk_usage=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
    if [ "$disk_usage" -lt 80 ]; then
        log "${GREEN}[+] Disk usage: ${disk_usage}%${NC}"
    elif [ "$disk_usage" -lt 90 ]; then
        log "${YELLOW}[!] Disk usage: ${disk_usage}% (Warning)${NC}"
        ((HEALTH_WARNINGS++))
    else
        log "${RED}[!] Disk usage: ${disk_usage}% (Critical)${NC}"
        ((HEALTH_ERRORS++))
        HEALTH_STATUS="UNHEALTHY"
    fi
    
    # Load average
    load_avg=$(uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | sed 's/,//')
    load_int=$(echo "$load_avg" | cut -d. -f1)
    if [ "$load_int" -lt 2 ]; then
        log "${GREEN}[+] Load average: $load_avg${NC}"
    elif [ "$load_int" -lt 5 ]; then
        log "${YELLOW}[!] Load average: $load_avg (High)${NC}"
        ((HEALTH_WARNINGS++))
    else
        log "${RED}[!] Load average: $load_avg (Critical)${NC}"
        ((HEALTH_ERRORS++))
        HEALTH_STATUS="UNHEALTHY"
    fi
    
    # Memory usage
    mem_usage=$(free | grep Mem | awk '{print ($3/$2) * 100.0}' | cut -d. -f1)
    if [ "$mem_usage" -lt 80 ]; then
        log "${GREEN}[+] Memory usage: ${mem_usage}%${NC}"
    elif [ "$mem_usage" -lt 90 ]; then
        log "${YELLOW}[!] Memory usage: ${mem_usage}% (High)${NC}"
        ((HEALTH_WARNINGS++))
    else
        log "${RED}[!] Memory usage: ${mem_usage}% (Critical)${NC}"
        ((HEALTH_ERRORS++))
        HEALTH_STATUS="UNHEALTHY"
    fi
}

# Check network connectivity
check_network_connectivity() {
    log "${BLUE}--- Checking Network Connectivity ---${NC}"
    
    # Check key network connections based on host type
    HOSTNAME=$(hostname)
    
    case "$HOSTNAME" in
        *bastion*|*rocky*)
            # Bastion should reach data1
            check_health "Network to Data1" "ping -c 1 -W 5 data1" "success"
            ;;
        *data1*|*ops*)
            # Data1 should reach bastion and control node
            # Get control node hostname from environment or use configured datasink's target
            CONTROL_NODE="${RAMROD_HOST:-buzz}"
            check_health "Network to Control Node" "ping -c 1 -W 5 $CONTROL_NODE" "success"
            ;;
        *)
            # Control node (ramrod) should reach data1 and production nodes
            if [ -d "~/sentinelcam/devops/ansible" ]; then
                check_health "Network to Data1" "ping -c 1 -W 5 data1" "success"
                
                # Check if we can reach any production nodes
                # Get first node from inventory for testing
                if [ -f "~/sentinelcam/devops/ansible/inventory/production.yaml" ]; then
                    first_node=$(ansible all -i ~/sentinelcam/devops/ansible/inventory/production.yaml --list-hosts | head -1 | xargs)
                    if [ -n "$first_node" ]; then
                        node_ip=$(echo "$first_node" | awk '{print $1}')
                        check_health "Network to Production Node ($node_ip)" "ping -c 1 -W 5 $node_ip" "success"
                    fi
                fi
            fi
            ;;
    esac
}

# Check service status
check_service_status() {
    log "${BLUE}--- Checking Service Status ---${NC}"
    
    # Check for SentinelCam services (only on production nodes, not control hosts)
    HOSTNAME=$(hostname)
    # Skip service checks on bastion and control nodes
    if [[ "$HOSTNAME" == *bastion* ]] || [[ "$HOSTNAME" == *rocky* ]] || [ -d "~/sentinelcam/devops/ansible/inventory" ]; then
        log "Skipping service checks on control/bastion host"
    else
        for service in imagenode camwatcher datapump sentinel; do
            if systemctl list-units --type=service | grep -q "$service"; then
                if systemctl is-active "$service" > /dev/null 2>&1; then
                    log "${GREEN}[+] Service $service: ACTIVE${NC}"
                else
                    log "${YELLOW}[!] Service $service: INACTIVE${NC}"
                    ((HEALTH_WARNINGS++))
                fi
            fi
        done
    fi
}

# Check ansible inventory consistency (control node only)
check_ansible_inventory() {
    log "${BLUE}--- Checking Ansible Inventory ---${NC}"
    
    if [ ! -f "~/sentinelcam/devops/ansible/inventory/production.yaml" ]; then
        log "${YELLOW}[!] Production inventory not found${NC}"
        ((HEALTH_WARNINGS++))
        return 1
    fi
    
    # Count hosts in inventory
    host_count=$(ansible all -i ~/sentinelcam/devops/ansible/inventory/production.yaml --list-hosts 2>/dev/null | grep -c -v "hosts")
    if [ -n "$host_count" ] && [ "$host_count" -gt 0 ]; then
        log "${GREEN}[+] Ansible inventory contains $host_count hosts${NC}"
    else
        log "${RED}[!] Ansible inventory appears empty or invalid${NC}"
        ((HEALTH_ERRORS++))
        HEALTH_STATUS="UNHEALTHY"
        return 1
    fi
    
    # Check inventory syntax
    if ansible-inventory -i ~/sentinelcam/devops/ansible/inventory/production.yaml --list > /dev/null 2>&1; then
        log "${GREEN}[+] Ansible inventory syntax valid${NC}"
    else
        log "${RED}[!] Ansible inventory syntax errors detected${NC}"
        ((HEALTH_ERRORS++))
        HEALTH_STATUS="UNHEALTHY"
    fi
}

# Generate health report
generate_health_report() {
    log "${BLUE}--- DEPLOYMENT HEALTH SUMMARY ---${NC}"
    log "Overall Status: $HEALTH_STATUS"
    log "Warnings: $HEALTH_WARNINGS"
    log "Errors: $HEALTH_ERRORS"
    log "Host: $(hostname)"
    log "Timestamp: $TIMESTAMP"
    log "Log File: $HEALTH_LOG"
    
    # Set exit code based on health status
    if [ "$HEALTH_STATUS" = "UNHEALTHY" ]; then
        log "${RED}System requires attention before deployment${NC}"
        return 1
    elif [ "$HEALTH_WARNINGS" -gt 0 ]; then
        log "${YELLOW}System functional with warnings${NC}"
        return 0
    else
        log "${GREEN}System healthy for deployment${NC}"
        return 0
    fi
}

# Main execution
main() {
    log "${BLUE}--- SentinelCam Deployment Health Monitor ---${NC}"
    log "Starting health check on $(hostname)"
    
    check_pipeline_status
    check_system_resources
    check_network_connectivity
    check_service_status
    
    # Check ansible inventory if on control node (ramrod)
    if [ -d "~/sentinelcam/devops/ansible/inventory" ]; then
        check_ansible_inventory
    fi
    
    generate_health_report
}

# Handle command line arguments
case "${1:-}" in
    --help|-h)
        echo "Usage: $0 [--help|--status-only|--quiet]"
        echo "  --help        Show this help message"
        echo "  --status-only Only check pipeline status, skip system checks" 
        echo "  --quiet       Suppress output, only log to file"
        exit 0
        ;;
    --status-only)
        log "${BLUE}--- SentinelCam Pipeline Status Check ---${NC}"
        check_pipeline_status
        generate_health_report
        ;;
    --quiet)
        # Redirect output to log file only
        exec > "$HEALTH_LOG" 2>&1
        main
        ;;
    *)
        main
        ;;
esac
