#!/bin/bash
# Bastion script: Process uploaded code and transfer to data1
# File: /home/rocky/scripts/process-code-upload.sh

UPLOAD_FILE="$1"
CACHE_DIR="/home/rocky/transfer_cache"
DATA1_USER="ops"
DATA1_HOST="data1"
LOG_FILE="$CACHE_DIR/logs/deployment.log"

# Ensure directories exist
mkdir -p "$CACHE_DIR"/{incoming,processed,logs}

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log "--- Processing upload: $UPLOAD_FILE ---"

if [ ! -f "$CACHE_DIR/incoming/$UPLOAD_FILE" ]; then
    log "ERROR: Upload file not found: $UPLOAD_FILE"
    exit 1
fi

cd "$CACHE_DIR/incoming"

# Extract and validate
log "Extracting package..."
EXTRACT_DIR="extracted_$(date +%s)"
mkdir -p "$EXTRACT_DIR"

if ! unzip -q "$UPLOAD_FILE" -d "$EXTRACT_DIR/"; then
    log "ERROR: Failed to extract package"
    exit 1
fi

log "[+] Package extracted to $EXTRACT_DIR"

# Verify package contents
if [ ! -d "$EXTRACT_DIR/devops" ]; then
    log "WARNING: No devops directory found in package"
fi

# Transfer to data1 staging (note: ZIP extraction preserves timestamps)
log "Transferring to data1..."
if ssh "$DATA1_USER@$DATA1_HOST" "mkdir -p /home/ops/sentinelcam/staging/incoming && rm -rf /home/ops/sentinelcam/staging/incoming/*"; then
    # Use rsync to preserve timestamps from ZIP extraction
    # The trailing slash on source ensures contents are transferred, not the directory itself
    if command -v rsync >/dev/null 2>&1; then
        if rsync -az --checksum --progress \
           "$EXTRACT_DIR/" "$DATA1_USER@$DATA1_HOST:/home/ops/sentinelcam/staging/incoming/"; then
            log "[+] Transfer to data1 successful ($(find "$EXTRACT_DIR" -type f | wc -l) files)"
        else
            log "ERROR: Failed to transfer to data1 via rsync"
            exit 1
        fi
    else
        # Fallback: scp with preservation - use wildcard to transfer contents
        if scp -rp "$EXTRACT_DIR"/* "$DATA1_USER@$DATA1_HOST:/home/ops/sentinelcam/staging/incoming/"; then
            log "[+] Transfer to data1 successful (scp)"
        else
            log "ERROR: Failed to transfer to data1"
            exit 1
        fi
    fi
else
    log "ERROR: Cannot access data1 staging directory"
    exit 1
fi

# Trigger data1 processing
# Trigger data1 integration
log "Triggering data1 integration..."
if ssh "$DATA1_USER@$DATA1_HOST" "/home/ops/scripts/integrate-code-update.sh"; then
    log "[+] Data1 integration triggered"
else
    log "WARNING: Data1 integration may have failed"
fi

# Move to processed
mv "$UPLOAD_FILE" "$CACHE_DIR/processed/"
log "[+] Upload file moved to processed"

# Cleanup extraction
rm -rf "$EXTRACT_DIR"
log "[+] Temporary files cleaned up"

log "--- Upload processing complete ---"

# Optional: Notify via simple status file
echo "$(date): $UPLOAD_FILE processed successfully" > "$CACHE_DIR/last_deployment.status"
