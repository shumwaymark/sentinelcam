#!/bin/bash
# Model Rollback Utility
# 
# Rollback a model to a previous version by updating the registry configuration
# and redeploying configs (models stay in place, only configs change).
#
# This script provides intelligent rollback with validation and safety checks:
#   - List available versions when no args provided
#   - Quick rollback to previous_version with --previous flag
#   - Validate target version exists before rollback
#   - Update model_registry.yaml atomically
#   - Trigger config-only redeployment
#   - Log all operations
#
# Usage:
#   ./rollback_model.sh                          # List all models and versions
#   ./rollback_model.sh <model_name>             # List versions for specific model
#   ./rollback_model.sh <model_name> <version>   # Rollback to specific version
#   ./rollback_model.sh <model_name> --previous  # Rollback to previous_version
#
# Examples:
#   ./rollback_model.sh face_recognition 2025-11-20
#   ./rollback_model.sh face_recognition --previous

set -e
set -o pipefail

# Configuration (update these paths for your deployment)
REGISTRY_PATH="/home/ops/sentinelcam/model_registry"
ANSIBLE_DIR="/home/ops/sentinelcam/current_deployment/devops/ansible"
REGISTRY_CONFIG="${ANSIBLE_DIR}/inventory/group_vars/all/model_registry.yaml"
LOG_FILE="/home/ops/sentinelcam/logs/model_deployment.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Validate prerequisites
if [ ! -d "$REGISTRY_PATH" ]; then
    echo -e "${RED}ERROR: Registry path does not exist: ${REGISTRY_PATH}${NC}"
    exit 1
fi

if [ ! -f "$REGISTRY_CONFIG" ]; then
    echo -e "${RED}ERROR: Registry config not found: ${REGISTRY_CONFIG}${NC}"
    exit 1
fi

# Function to extract current/previous versions from registry config
get_version() {
    local model_name="$1"
    local version_type="$2"  # current_version or previous_version
    
    grep -A2 "^  ${model_name}:" "$REGISTRY_CONFIG" | \
        grep "${version_type}:" | \
        awk '{print $2}' | \
        tr -d '"' | \
        head -n1
}

# Function to list all available versions for a model
list_versions() {
    local model_name="$1"
    local model_path="${REGISTRY_PATH}/${model_name}"
    
    if [ ! -d "$model_path" ]; then
        echo -e "${RED}ERROR: Model not found: ${model_name}${NC}"
        return 1
    fi
    
    current=$(get_version "$model_name" "current_version")
    previous=$(get_version "$model_name" "previous_version")
    
    echo -e "${BLUE}Model: ${model_name}${NC}"
    echo -e "  ${GREEN}Current version: ${current}${NC}"
    echo -e "  ${YELLOW}Previous version: ${previous}${NC}"
    echo ""
    echo "Available versions:"
    
    for version_dir in $(find "$model_path" -mindepth 1 -maxdepth 1 -type d | sort -r); do
        version=$(basename "$version_dir")
        manifest="${version_dir}/manifest.yaml"
        
        marker=""
        if [ "$version" = "$current" ]; then
            marker="${GREEN}[CURRENT]${NC}"
        elif [ "$version" = "$previous" ]; then
            marker="${YELLOW}[PREVIOUS]${NC}"
        fi
        
        if [ -f "$manifest" ]; then
            deployed=$(grep "date_deployed:" "$manifest" | awk '{print $2}' || echo "unknown")
            echo -e "  - ${version} ${marker} (deployed: ${deployed})"
        else
            echo -e "  - ${version} ${marker}"
        fi
    done
}

# Function to list all models
list_all_models() {
    echo -e "${BLUE}Available models in registry:${NC}"
    echo ""
    
    for model_dir in "${REGISTRY_PATH}"/*; do
        if [ ! -d "$model_dir" ]; then
            continue
        fi
        
        model_name=$(basename "$model_dir")
        current=$(get_version "$model_name" "current_version")
        previous=$(get_version "$model_name" "previous_version")
        
        echo -e "${BLUE}${model_name}${NC}"
        echo -e "  Current:  ${GREEN}${current}${NC}"
        echo -e "  Previous: ${YELLOW}${previous}${NC}"
        echo ""
    done
    
    echo "Usage: $0 <model_name> [version|--previous]"
}

# Function to perform rollback
rollback_model() {
    local model_name="$1"
    local target_version="$2"
    
    # Validate model exists
    if [ ! -d "${REGISTRY_PATH}/${model_name}" ]; then
        echo -e "${RED}ERROR: Model not found: ${model_name}${NC}"
        exit 1
    fi
    
    # Get current versions
    current=$(get_version "$model_name" "current_version")
    previous=$(get_version "$model_name" "previous_version")
    
    # Handle --previous flag
    if [ "$target_version" = "--previous" ]; then
        if [ -z "$previous" ] || [ "$previous" = "null" ]; then
            echo -e "${RED}ERROR: No previous version available for ${model_name}${NC}"
            exit 1
        fi
        target_version="$previous"
        echo -e "${YELLOW}Rolling back to previous version: ${target_version}${NC}"
    fi
    
    # Validate target version exists
    if [ ! -d "${REGISTRY_PATH}/${model_name}/${target_version}" ]; then
        echo -e "${RED}ERROR: Version not found: ${target_version}${NC}"
        echo "Available versions:"
        list_versions "$model_name"
        exit 1
    fi
    
    # Check if already at target version
    if [ "$current" = "$target_version" ]; then
        echo -e "${YELLOW}Model ${model_name} is already at version ${target_version}${NC}"
        exit 0
    fi
    
    # Confirm rollback
    echo -e "${YELLOW}Rollback summary:${NC}"
    echo "  Model: ${model_name}"
    echo -e "  Current version: ${GREEN}${current}${NC}"
    echo -e "  Target version:  ${BLUE}${target_version}${NC}"
    echo ""
    echo -n "Proceed with rollback? [y/N] "
    read -r confirm
    
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Rollback cancelled"
        exit 0
    fi
    
    log "===== Rolling back model: ${model_name} ====="
    log "From version: ${current}"
    log "To version: ${target_version}"
    
    # Backup registry config
    cp "$REGISTRY_CONFIG" "${REGISTRY_CONFIG}.bak"
    log "Backed up registry config"
    
    # Update registry config
    # This is a simplified sed approach - in production you might want to use yq or Python
    sed -i "/${model_name}:/,/previous_version:/ {
        s/current_version: .*/current_version: \"${target_version}\"/
        s/previous_version: .*/previous_version: \"${current}\"/
    }" "$REGISTRY_CONFIG"
    
    log "Updated registry config"
    
    # Commit to git
    cd "$ANSIBLE_DIR"
    git add inventory/group_vars/all/model_registry.yaml
    git commit -m "Rollback ${model_name} from ${current} to ${target_version}" || true
    log "Committed changes to git"
    
    # Deploy configs (models already in place, just update configs and restart services)
    echo -e "${BLUE}Deploying configuration changes...${NC}"
    ansible-playbook playbooks/deploy-models.yaml --tags="${model_name}" 2>&1 | tee -a "$LOG_FILE"
    
    log "===== Rollback completed successfully ====="
    echo -e "${GREEN}Model ${model_name} rolled back to version ${target_version}${NC}"
    echo ""
    echo -e "Previous version is now: ${YELLOW}${current}${NC}"
    echo "To undo this rollback, run:"
    echo -e "  ${0} ${model_name} ${current}"
}

# Main script logic
if [ $# -eq 0 ]; then
    # No arguments - list all models
    list_all_models
    exit 0
fi

if [ $# -eq 1 ]; then
    # One argument - list versions for specific model
    list_versions "$1"
    exit 0
fi

if [ $# -eq 2 ]; then
    # Two arguments - perform rollback
    rollback_model "$1" "$2"
    exit 0
fi

# Invalid usage
echo "Usage: $0 [model_name] [version|--previous]"
exit 1
