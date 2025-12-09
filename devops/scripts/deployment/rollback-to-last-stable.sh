#!/bin/bash
# Rollback to last stable deployment
# Usage: ./rollback-to-last-stable.sh
# Run on data1 node

CURRENT_DIR="/home/ops/sentinelcam/current_deployment"
BACKUP_DIR="/home/ops/sentinelcam/backups"
RAMROD_HOST="buzz"
RAMROD_USER="pi"

echo "[?] Finding latest backup checkpoint..."

# Find the most recent backup
LATEST_BACKUP=$(ls -t "$BACKUP_DIR" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}' | head -1)

if [ -z "$LATEST_BACKUP" ]; then
    echo "[!] No backup checkpoints available"
    exit 1
fi

BACKUP_PATH="$BACKUP_DIR/$LATEST_BACKUP"

if [ ! -d "$BACKUP_PATH" ]; then
    echo "[!] Backup directory not found: $BACKUP_PATH"
    exit 1
fi

echo "[<] Rolling back to checkpoint: $LATEST_BACKUP"
echo "[*] Backup file: $BACKUP_PATH"

# Confirm rollback
read -p "[!] This will replace current source with backup. Continue? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "[!] Rollback cancelled"
    exit 1
fi

# Stop services via ansible on ramrod node
echo "[X] Stopping services on target nodes..."
echo "[*] Triggering service stop on ${RAMROD_HOST}..."
ssh ${RAMROD_USER}@${RAMROD_HOST} "cd /home/pi/sentinelcam/devops/ansible && ansible-playbook -i inventory/production.yaml playbooks/stop-services.yaml" 2>/dev/null || true

# Create backup of current state before rollback
ROLLBACK_TIMESTAMP=$(date '+%Y-%m-%d_%H-%M')
echo "[*] Backing up current state before rollback..."
mkdir -p "$BACKUP_DIR/pre-rollback-$ROLLBACK_TIMESTAMP"
if [ -d "$CURRENT_DIR" ]; then
    rsync -a "$CURRENT_DIR/" "$BACKUP_DIR/pre-rollback-$ROLLBACK_TIMESTAMP/"
    echo "[+] Current state backed up"
fi

# Restore from backup
echo "[*] Restoring from backup: $LATEST_BACKUP..."
if [ -d "$CURRENT_DIR" ]; then
    rm -rf "$CURRENT_DIR"/*
fi
mkdir -p "$CURRENT_DIR"

if rsync -a "$BACKUP_PATH/" "$CURRENT_DIR/"; then
    echo "[+] Deployment restored from backup"
else
    echo "[!] Failed to restore from backup"
    exit 1
fi

# Redeploy the rolled-back version
ROLLBACK_ID="rollback_${ROLLBACK_TIMESTAMP}_to_${LATEST_BACKUP}"
echo "[>] Redeploying rolled-back version..."
echo "[*] Triggering deployment on ${RAMROD_HOST}..."
if ssh ${RAMROD_USER}@${RAMROD_HOST} "/home/pi/sentinelcam/devops/scripts/sync/sync-ramrod-from-datasink.sh $ROLLBACK_ID"; then
    echo "[+] Rollback deployment triggered successfully"
    echo "[*] Rollback ID: $ROLLBACK_ID"
    echo "[~] Services should restart automatically"
else
    echo "[!] Failed to trigger rollback deployment"
    echo "[*] You may need to manually trigger deployment on ${RAMROD_HOST}"
    echo "[*] Manual: ssh ${RAMROD_USER}@${RAMROD_HOST} /home/pi/sentinelcam/devops/scripts/sync/sync-ramrod-from-datasink.sh"
fi

echo ""
echo "[+] Rollback complete!"
echo "[*] Restored to: $LATEST_BACKUP"
echo "[*] Current state backed up to: pre-rollback-$ROLLBACK_TIMESTAMP"
echo "[*] Monitor system to ensure services are running correctly"
