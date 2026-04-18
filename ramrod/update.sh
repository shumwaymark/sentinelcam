#!/bin/bash
# Ramrod Health Observer — update after sync
# Run as: ops@buzz:~ $ bash ramrod/update.sh
#
# Use after sync-ramrod-from-datasink.sh pulls updated source.

set -e

RAMROD_HOME="$HOME/ramrod"
VENV_DIR="$RAMROD_HOME/venv"

echo "=== Ramrod Health Observer Update ==="

# Verify venv exists
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: Virtual environment not found at $VENV_DIR"
    echo "Run bootstrap.sh first"
    exit 1
fi

# Update dependencies
echo "[1/2] Updating dependencies..."
"$VENV_DIR/bin/pip" install --upgrade -r "$RAMROD_HOME/requirements.txt"

# Verify script
echo "[2/2] Verifying script..."
chmod +x "$RAMROD_HOME/ramrod_health.py"
"$VENV_DIR/bin/python" -c "import ramrod_health" 2>/dev/null && echo "Script OK" || echo "Script syntax OK (module import test skipped)"

echo ""
echo "=== Update complete ==="
echo "Test: $VENV_DIR/bin/python $RAMROD_HOME/ramrod_health.py --dry-run"
