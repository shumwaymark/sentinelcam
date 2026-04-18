#!/bin/bash
# Ramrod Health Observer — one-time bootstrap on buzz
# Run as: ops@buzz:~ $ bash ramrod/bootstrap.sh
#
# Prerequisites: Python 3.11+, pip, ansible-core installed

set -e

RAMROD_HOME="$HOME/ramrod"
VENV_DIR="$RAMROD_HOME/venv"
LOG_DIR="$RAMROD_HOME/logs"
SCRIPT="$RAMROD_HOME/ramrod_health.py"

echo "=== Ramrod Health Observer Bootstrap ==="
echo "Home: $RAMROD_HOME"

# Create directory structure
echo "[1/5] Creating directories..."
mkdir -p "$LOG_DIR"

# Create virtual environment
echo "[2/5] Creating Python virtual environment..."
python3 -m venv "$VENV_DIR"

# Install dependencies
echo "[3/5] Installing dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$RAMROD_HOME/requirements.txt"

# Verify script is present
echo "[4/5] Verifying health check script..."
if [ ! -f "$SCRIPT" ]; then
    echo "ERROR: $SCRIPT not found"
    echo "Ensure ramrod/ source has been synced from datasink"
    exit 1
fi
chmod +x "$SCRIPT"

# Install cron job (every 3 minutes)
echo "[5/5] Installing cron job..."
CRON_CMD="$VENV_DIR/bin/python $SCRIPT 2>&1 | tail -1 >> $LOG_DIR/cron.log"
CRON_LINE="*/3 * * * * $CRON_CMD"

# Add cron entry if not already present
# Note: grep -v exits 1 when no lines match (empty crontab), so || true is needed
( (crontab -l 2>/dev/null || true) | (grep -v "ramrod_health.py" || true); echo "$CRON_LINE") | crontab -

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Next steps:"
echo "  1. Render configuration from Ansible inventory:"
echo "     cd ~/sentinelcam/devops/ansible"
echo "     ansible-playbook playbooks/configure-ramrod.yaml --connection=local"
echo ""
echo "  2. Test with dry-run:"
echo "     $VENV_DIR/bin/python $SCRIPT --dry-run"
echo ""
echo "  3. Verify cron is installed:"
echo "     crontab -l | grep ramrod"
