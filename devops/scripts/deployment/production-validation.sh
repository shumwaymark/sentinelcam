#!/bin/bash
# production-validation.sh - Pre-deployment validation on the ramrod node
# Runs on buzz before Ansible deployment to target nodes
#
# Usage:
#   production-validation.sh                    # Validate all sentinelcam_nodes
#   production-validation.sh datasinks          # Validate only datasinks group
#   production-validation.sh outposts sentinels # Validate specific groups

# Configuration
ANSIBLE_HOME="/home/pi/sentinelcam/devops/ansible"
VALIDATION_LOG="$ANSIBLE_HOME/logs/production_validation_$(date +%Y%m%d_%H%M%S).log"
INVENTORY="$ANSIBLE_HOME/inventory/production.yaml"

# Target groups: passed as arguments, default to sentinelcam_nodes
TARGET_GROUPS="${@:-sentinelcam_nodes}"

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$VALIDATION_LOG"
}

mkdir -p "$(dirname "$VALIDATION_LOG")"

log "Starting production validation on $(hostname)..."
log "Validation log: $VALIDATION_LOG"
log "Target groups: $TARGET_GROUPS"

# --- Validate System Resources ---
log "--- Validating System Resources ---"

disk_usage=$(df /home | awk 'NR==2 {print $5}' | sed 's/%//')
log "Disk usage: ${disk_usage}%"
if [ "$disk_usage" -gt 90 ]; then
    log "ERROR: Disk usage too high: ${disk_usage}%"
    exit 1
fi
log "[+] Disk usage acceptable: ${disk_usage}%"

mem_usage=$(free | awk 'NR==2{printf "%.0f", $3*100/$2}')
log "Memory usage: ${mem_usage}%"
if [ "$mem_usage" -gt 95 ]; then
    log "ERROR: Memory usage too high: ${mem_usage}%"
    exit 1
fi
log "[+] Memory usage acceptable: ${mem_usage}%"

load_avg=$(uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | sed 's/,//')
log "Load average: $load_avg"
log "[+] System load acceptable: $load_avg"

# --- Validate Ansible Connectivity ---
log "--- Validating Ansible Connectivity ---"

if [ ! -f "$INVENTORY" ]; then
    log "ERROR: Ansible inventory not found: $INVENTORY"
    exit 1
fi

# Ping only the target groups, not the entire inventory
# This prevents failures from nodes irrelevant to the current deployment
validation_failed=false
for group in $TARGET_GROUPS; do
    log "Testing connectivity to group: $group..."
    if ansible "$group" -i "$INVENTORY" -m ping --timeout 5 --one-line 2>/dev/null; then
        log "[+] Group $group is reachable"
    else
        log "WARNING: Some nodes in $group are unreachable"
        validation_failed=true
    fi
done

if [ "$validation_failed" = true ]; then
    log "ERROR: Connectivity validation failed for one or more target groups"
    exit 1
fi

log "[+] All target nodes are reachable"
log "[+] Production validation passed"
