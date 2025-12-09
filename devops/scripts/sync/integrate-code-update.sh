#!/bin/bash
# Datasink script: Integrate uploaded code using current ansible structure
# File: /home/ops/scripts/integrate-code-update.sh

# Node configuration - canonical names
DATASINK_USER="ops"
DATASINK_GROUP="ops"
RAMROD_USER="pi"
RAMROD_HOST="buzz"  # Hostname for ramrod node

# Paths match current implementation
STAGING_DIR="/home/$DATASINK_USER/sentinelcam/staging/incoming"
CURRENT_DEPLOYMENT="/home/$DATASINK_USER/sentinelcam/current_deployment"
DEVOPS_DIR="/home/$DATASINK_USER/sentinelcam/devops"
DEVOPS_ANSIBLE="$DEVOPS_DIR/ansible"
DEVOPS_SCRIPTS="$DEVOPS_DIR/scripts"
BACKUP_DIR="/home/$DATASINK_USER/sentinelcam/backups"
LOG_FILE="/home/$DATASINK_USER/sentinelcam/logs/integration.log"
DEPLOY_FLAGS_DIR="/home/$DATASINK_USER/sentinelcam/staging/.deploy_flags"
CONFIGS_DIR="/home/$DATASINK_USER/sentinelcam/configs"
MANIFEST_FILE="/home/$DATASINK_USER/sentinelcam/last_deployment.manifest"
COMPLETION_FLAG="/home/$DATASINK_USER/sentinelcam/.integration_complete"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

# Ensure directories exist
mkdir -p "$CURRENT_DEPLOYMENT" "$DEVOPS_DIR" "$DEVOPS_ANSIBLE" "$DEVOPS_SCRIPTS" "$BACKUP_DIR" "$(dirname "$LOG_FILE")"

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log "--- Starting code integration ---"

# Check if there's anything to process
if [ ! "$(ls -A "$STAGING_DIR" 2>/dev/null)" ]; then
    log "No files in staging directory"
    exit 0
fi

# Create backup of current deployment
log "Creating backup of current deployment..."
if [ -d "$CURRENT_DEPLOYMENT" ]; then
    tar -czf "$BACKUP_DIR/deployment_backup_$TIMESTAMP.tar.gz" -C "$(dirname "$CURRENT_DEPLOYMENT")" "$(basename "$CURRENT_DEPLOYMENT")" 2>/dev/null
    log "[+] Backup created: deployment_backup_$TIMESTAMP.tar.gz"
fi

# Create backup of current devops/ansible
if [ -d "$DEVOPS_ANSIBLE" ]; then
    tar -czf "$BACKUP_DIR/ansible_backup_$TIMESTAMP.tar.gz" -C "$(dirname "$DEVOPS_DIR")" "devops" 2>/dev/null
    log "[+] Ansible backup created: ansible_backup_$TIMESTAMP.tar.gz"
fi

# Process component updates and create deployment flags
mkdir -p "$DEPLOY_FLAGS_DIR"

# Define component mappings for current structure
declare -A COMPONENT_PATHS=(
    ["imagenode"]="imagenode"
    ["camwatcher"]="camwatcher"
    ["datapump"]="camwatcher" 
    ["sentinel"]="sentinel"
    ["watchtower"]="watchtower"
    ["imagehub"]="imagehub"
)

for component in "${!COMPONENT_PATHS[@]}"; do
    staging_path="$STAGING_DIR/$component"
    deploy_path="$CURRENT_DEPLOYMENT/${COMPONENT_PATHS[$component]}"
    
    if [ -d "$staging_path" ]; then
        log "Updating $component source at ${COMPONENT_PATHS[$component]}..."
        mkdir -p "$deploy_path"
        
        # Sync with enhanced preservation for Ansible change detection
        if rsync -az --checksum "$staging_path/" "$deploy_path/"; then
            log "[+] $component source updated with timestamp preservation"
            
            # Create deployment flag - use correct naming for current playbooks
            case $component in
                "imagenode")
                    touch "$DEPLOY_FLAGS_DIR/outpost"
                    log "[+] Deployment flag created for outpost (imagenode)"
                    ;;
                *)
                    touch "$DEPLOY_FLAGS_DIR/$component"
                    log "[+] Deployment flag created for $component"
                    ;;
            esac
        else
            log "ERROR: Failed to update $component source"
        fi
    fi
done

# Process devops structure updates - ansible and scripts
if [ -d "$STAGING_DIR/devops" ]; then
    log "Updating devops structure (ansible + scripts)..."
    
    # Ensure devops directory structure exists
    mkdir -p "$DEVOPS_DIR"
    
    # Sync entire devops structure with enhanced timestamp preservation
    if rsync -az --checksum "$STAGING_DIR/devops/" "$DEVOPS_DIR/"; then
        log "[+] DevOps structure updated with timestamp preservation (ansible + scripts)"
        
        # CRITICAL: Fix permissions immediately after rsync from Windows source
        log "Fixing permissions from Windows filesystem extraction..."
        
        # Set correct ownership for entire devops structure
        chown -R $DATASINK_USER:$DATASINK_GROUP "$DEVOPS_DIR/"
        
        # Fix directory permissions (755 - executable for traversal)
        find "$DEVOPS_DIR/" -type d -exec chmod 755 {} \;
        
        # Fix file permissions (644 - not executable)
        find "$DEVOPS_DIR/" -type f -exec chmod 644 {} \;
        
        # Make shell scripts executable (755)
        find "$DEVOPS_DIR/" -name "*.sh" -exec chmod 755 {} \;
        
        # Remove Windows carriage return characters from script files
        #find "$DEVOPS_DIR/scripts" -name "*.sh" | xargs sed -i 's/\r$//'
        
        # Remove Windows carriage return characters from YAML files
        #find "$DEVOPS_DIR/ansible" -name "*.yaml" | xargs sed -i 's/\r$//'

        log "[+] Permissions fixed for Windows-sourced files"
        
        # Verify critical playbooks are present
        expected_playbooks=(
            "deploy-datapump.yaml"
            "deploy-outpost.yaml"
            "deploy-camwatcher.yaml"
            "deploy-watchtower.yaml"
            "deploy-sentinel.yaml"
        )
        
        missing_playbooks=()
        for playbook in "${expected_playbooks[@]}"; do
            if [ ! -f "$DEVOPS_ANSIBLE/playbooks/$playbook" ]; then
                missing_playbooks+=("$playbook")
            fi
        done
        
        if [ ${#missing_playbooks[@]} -gt 0 ]; then
            log "WARNING: Missing expected playbooks: ${missing_playbooks[*]}"
        else
            log "[+] All expected current playbooks are present"
        fi
        
        # Verify scripts directory exists
        if [ -d "$DEVOPS_SCRIPTS" ]; then
            script_count=$(find "$DEVOPS_SCRIPTS" -name "*.sh" | wc -l)
            log "[+] DevOps scripts directory present with $script_count shell scripts"
        else
            log "WARNING: DevOps scripts directory not found after sync"
        fi
        
        # Create deployment flag for ansible
        touch "$DEPLOY_FLAGS_DIR/ansible"
        log "[+] Deployment flag created for ansible"
    else
        log "ERROR: Failed to update devops structure"
    fi
fi

# Legacy handling: Process old devops/ansible location for backward compatibility
if [ -d "$STAGING_DIR/devops/ansible" ] && [ ! -d "$STAGING_DIR/devops/scripts" ]; then
    log "Processing legacy devops/ansible-only structure..."
    
    # Convert legacy structure to current devops/ansible structure
    mkdir -p "$DEVOPS_ANSIBLE"
    if rsync -az --checksum "$STAGING_DIR/ansible/" "$DEVOPS_ANSIBLE/"; then
        log "[+] Legacy ansible converted to devops/ansible structure"
        touch "$DEPLOY_FLAGS_DIR/ansible"
    else
        log "ERROR: Failed to process legacy ansible structure"
    fi
fi

# Testing infrastructure - placeholder for future implementation
log "Automated testing not yet implemented - skipping test phase"
echo "$(date '+%Y-%m-%d %H:%M:%S') - TESTS_SKIPPED" >> "$DEPLOY_FLAGS_DIR/test_status"

# Process configuration updates
if [ -d "$STAGING_DIR/configs" ]; then
    log "Updating configuration templates..."
    mkdir -p "$CONFIGS_DIR"
    
    if rsync -az --checksum "$STAGING_DIR/configs/" "$CONFIGS_DIR/"; then
        log "[+] Configuration templates updated"
    else
        log "ERROR: Failed to update configuration templates"
    fi
fi

# Set ownership and permissions for current structure
log "Setting ownership and permissions..."
chown -R $DATASINK_USER:$DATASINK_GROUP "$CURRENT_DEPLOYMENT" "$DEVOPS_ANSIBLE" 2>/dev/null

# Set directory permissions only (allow traversal, don't touch file permissions)
find "$CURRENT_DEPLOYMENT" -type d -exec chmod 755 {} \; 2>/dev/null
find "$DEVOPS_ANSIBLE" -type d -exec chmod 755 {} \; 2>/dev/null

# Update requirements.txt location for current ansible implementation
if [ -f "$STAGING_DIR/requirements.txt" ]; then
    cp "$STAGING_DIR/requirements.txt" "$CURRENT_DEPLOYMENT/"
    log "[+] requirements.txt updated in current_deployment"
fi

# Notify ramrod of updates...
log "Notifying ramrod of updates..."
if ssh $RAMROD_USER@$RAMROD_HOST "/home/$RAMROD_USER/sentinelcam/devops/scripts/sync/sync-ramrod-from-datasink.sh"; then
    log "[+] Ramrod notified to sync and deploy"
else
    log "WARNING: Failed to notify ramrod"
fi

# Cleanup staging
log "Cleaning up staging area..."
rm -rf "$STAGING_DIR"/*
log "[+] Staging area cleaned"

# Create deployment manifest with current structure information
cat > "$MANIFEST_FILE" << EOF
Deployment Time: $(date)
Backup Created: deployment_backup_$TIMESTAMP.tar.gz
Ansible Backup: ansible_backup_$TIMESTAMP.tar.gz
Current Deployment Path: $CURRENT_DEPLOYMENT
DevOps Ansible Path: $DEVOPS_ANSIBLE
Components Updated: $(find "$CURRENT_DEPLOYMENT" -maxdepth 2 -type d -name "imagenode" -o -name "camwatcher" -o -name "datapump" -o -name "sentinel" -o -name "watchtower" -o -name "imagehub" | wc -l)
Ansible Structure: $([ -d "$DEVOPS_ANSIBLE/playbooks" ] && echo "Current (devops/ansible)" || echo "Legacy")
Code-Only Playbooks Available: $(find "$DEVOPS_ANSIBLE/playbooks" -name "*-code-only.yaml" 2>/dev/null | wc -l)
Status: Complete
EOF

log "--- Code integration complete ---"

# Signal completion
touch "$COMPLETION_FLAG"
