#!/bin/bash
# Ramrod script: Sync ansible content from datasink and deploy
# File: /home/pi/scripts/sync-from-datasink.sh

# Node configuration - canonical names
RAMROD_USER="pi"
DATASINK_USER="ops"
DATASINK_HOST="data1"  # Hostname for datasink node

# Paths for current ansible structure
SENTINELCAM_HOME="/home/$RAMROD_USER/sentinelcam"
ANSIBLE_HOME="$SENTINELCAM_HOME/devops/ansible"
DATASINK_SOURCE="/home/$DATASINK_USER/sentinelcam/current_deployment"
DATASINK_DEVOPS="/home/$DATASINK_USER/sentinelcam/devops"
LOG_FILE="$ANSIBLE_HOME/logs/deployment.log"

# Ensure directories exist
mkdir -p "$ANSIBLE_HOME/logs"

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log "--- Syncing ansible from datasink ---"

# Check if datasink devops structure is accessible
if ! ssh -o ConnectTimeout=5 $DATASINK_USER@$DATASINK_HOST "test -d $DATASINK_DEVOPS/ansible"; then
    log "ERROR: Cannot access datasink devops/ansible directory: $DATASINK_DEVOPS/ansible"
    log "This suggests the devops structure hasn't been synced to datasink yet"
    exit 1
fi

# Note: Test infrastructure not yet implemented - proceeding with deployment
log "Note: Automated testing not yet enabled"

# Ensure local ansible structure matches current implementation
mkdir -p "$ANSIBLE_HOME"/{playbooks,roles,inventory,logs}

# Sync complete devops structure from datasink
log "Syncing complete devops structure from datasink..."

# Primary sync: Complete devops directory structure with clean output
rsync -az --checksum --delete --itemize-changes --ignore-errors \
      --exclude='logs/*' --exclude='*.retry' --exclude='.git*' \
      --filter='protect group_vars/infrastructure/vault.yaml' \
      $DATASINK_USER@$DATASINK_HOST:$DATASINK_DEVOPS/ "$SENTINELCAM_HOME/devops/" | \
      grep -E '^[>.]f' | sed 's/^[>.]f[^ ]* /  /' | while read file; do
          log "Updated: $file"
      done
    
log "[+] Complete devops structure synced (ansible + scripts)"

# Verify the ansible structure is complete
if [ ! -d "$ANSIBLE_HOME/playbooks" ] || [ ! -d "$ANSIBLE_HOME/roles" ]; then
    log "ERROR: Ansible structure incomplete after sync"
    exit 1
fi

# Verify we have the expected current playbooks
EXPECTED_PLAYBOOKS=(
    "deploy-datapump.yaml"
    "deploy-outpost.yaml" 
    "deploy-camwatcher.yaml"
    "deploy-watchtower.yaml"
    "deploy-sentinel.yaml"
)

missing_playbooks=()
for playbook in "${EXPECTED_PLAYBOOKS[@]}"; do
    if [ ! -f "$ANSIBLE_HOME/playbooks/$playbook" ]; then
        missing_playbooks+=("$playbook")
    fi
done

if [ ${#missing_playbooks[@]} -gt 0 ]; then
    log "WARNING: Missing expected playbooks: ${missing_playbooks[*]}"
    log "This may indicate sync issues or outdated datasink content"
fi

# Verify inventory structure exists
if [ ! -f "$ANSIBLE_HOME/inventory/production.yaml" ]; then
    log "ERROR: production.yaml inventory not found"
    log "Current ansible implementation requires proper inventory structure"
    exit 1
fi

log "[+] Ansible structure verification complete"

# Run production validation before deployment
log "Running production validation..."
if [ -f "$SENTINELCAM_HOME/devops/scripts/deployment/production-validation.sh" ]; then
    if "$SENTINELCAM_HOME/devops/scripts/deployment/production-validation.sh"; then
        log "[+] Production validation passed"
    else
        log "ERROR: Production validation failed"
        exit 1
    fi
else
    log "WARNING: Production validation script not found at expected location"
fi

# Determine what components need deployment based on deploy flags
log "Checking deployment flags from datasink..."
deploy_components=()

# Check which components have deployment flags
for component in datapump camwatcher outpost watchtower sentinel; do
    if ssh $DATASINK_USER@$DATASINK_HOST "test -f /home/$DATASINK_USER/sentinelcam/staging/.deploy_flags/$component"; then
        deploy_components+=("$component")
        log "[+] Deployment flag found for: $component"
    fi
done

if [ ${#deploy_components[@]} -eq 0 ]; then
    log "No deployment flags found - performing full deployment as fallback"
    deploy_components=("datapump" "camwatcher" "outpost" "watchtower" "sentinel")
fi

# Execute deployments using current ansible implementation
log "Executing deployments using current playbook structure..."
cd "$ANSIBLE_HOME"

# Check ansible connectivity first
if ansible all -i inventory/production.yaml -m ping --one-line > /dev/null 2>&1; then
    log "[+] Ansible connectivity verified"
else
    log "ERROR: Ansible connectivity check failed"
    exit 1
fi

# Deploy each flagged component using appropriate code-only playbook
deployment_success=true
for component in "${deploy_components[@]}"; do
    case $component in
        "datapump")
            playbook="playbooks/deploy-datapump.yaml"
            ;;
        "camwatcher") 
            playbook="playbooks/deploy-camwatcher.yaml"
            ;;
        "outpost")
            playbook="playbooks/deploy-outpost.yaml"
            ;;
        "watchtower")
            playbook="playbooks/deploy-watchtower.yaml"
            ;;
        "sentinel")
            playbook="playbooks/deploy-sentinel.yaml"
            ;;
        *)
            log "WARNING: Unknown component $component, skipping"
            continue
            ;;
    esac
    
    if [ -f "$playbook" ]; then
        log "Deploying $component using $playbook..."
        
        if ansible-playbook -i inventory/production.yaml "$playbook" --tags deploy; then
            log "[+] $component deployment completed successfully"
        else
            log "ERROR: $component deployment failed"
            deployment_success=false
        fi
    else
        log "WARNING: Playbook not found: $playbook"
        deployment_success=false
    fi
done

# Overall deployment status
if [ "$deployment_success" = true ]; then
    log "[+] All deployments completed successfully"
    echo "$(date): All deployments completed successfully" > "$ANSIBLE_HOME/logs/last_deployment.status"
else
    log "[!] Some deployments failed"
    echo "$(date): Some deployments failed" > "$ANSIBLE_HOME/logs/last_deployment.status"
    exit 1
fi

# Clear deployment flags on datasink after successful deployment
for component in "${deploy_components[@]}"; do
    ssh $DATASINK_USER@$DATASINK_HOST "rm -f /home/$DATASINK_USER/sentinelcam/staging/.deploy_flags/$component" 2>/dev/null || true
done

log "--- Sync and deployment complete ---"

# Send completion notification back to datasink
ssh $DATASINK_USER@$DATASINK_HOST "echo '$(date): Ramrod deployment complete - all components' >> /home/$DATASINK_USER/sentinelcam/logs/deployment_status.log" 2>/dev/null || true
