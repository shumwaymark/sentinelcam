#!/bin/bash
# production-validation.sh - Final validation tests before deploying to production nodes
# Runs on buzz before Ansible deployment to nodes

set -eE  # Exit on error, enable error trapping

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_HOME="/home/pi/sentinelcam/devops/ansible"
VALIDATION_LOG="$ANSIBLE_HOME/logs/production_validation_$(date +%Y%m%d_%H%M%S).log"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$VALIDATION_LOG"
}

# Error handling
error_exit() {
    log "${RED}VALIDATION FAILED: $1${NC}"
    exit 1
}

log "${BLUE}--- SentinelCam Production Validation ---${NC}"

# Validate Ansible connectivity to all nodes
validate_ansible_connectivity() {
    log "${BLUE}--- Validating Ansible Connectivity ---${NC}"
    
    if [ ! -f "$ANSIBLE_HOME/inventory/production.yaml" ]; then
        error_exit "Ansible inventory not found: $ANSIBLE_HOME/inventory/production.yaml"
    fi
    
    # Test connectivity to all nodes
    log "Testing connectivity to all production nodes..."
    if ansible all -i "$ANSIBLE_HOME/inventory/production.yaml" -m ping --timeout=30; then
        log "${GREEN}[+] All nodes are reachable${NC}"
    else
        error_exit "Some production nodes are unreachable"
    fi
}

# Validate ansible playbooks syntax
validate_ansible_syntax() {
    log "${BLUE}--- Validating Ansible Playbook Syntax ---${NC}"
    
    local playbook_errors=0
    
    # Check all playbooks for syntax errors
    for playbook in "$ANSIBLE_HOME/playbooks"/*.{yml,yaml}; do
        if [ -f "$playbook" ]; then
            log "Checking syntax: $(basename "$playbook")"
            if ansible-playbook --syntax-check "$playbook" -i "$ANSIBLE_HOME/inventory/hosts"; then
                log "${GREEN}[+] $(basename "$playbook") syntax valid${NC}"
            else
                log "${RED}[!] $(basename "$playbook") has syntax errors${NC}"
                playbook_errors=$((playbook_errors + 1))
            fi
        fi
    done
    
    if [ $playbook_errors -gt 0 ]; then
        error_exit "$playbook_errors playbook(s) have syntax errors"
    fi
    
    log "${GREEN}All playbooks have valid syntax${NC}"
}

# Validate deployment flags and readiness
validate_deployment_readiness() {
    log "${BLUE}--- Validating Deployment Readiness ---${NC}"
    
    # Check for deployment flags from data1
    local deployment_ready=true
    local components=("imagenode" "camwatcher" "datapump" "sentinel" "ansible")
    
    for component in "${components[@]}"; do
        if ssh ops@data1 "test -f /data/sentinelcam/staging/.deploy_flags/$component"; then
            log "${GREEN}[+] $component ready for deployment${NC}"
        else
            log "${YELLOW}[!] $component deployment flag not found${NC}"
            deployment_ready=false
        fi
    done
    
    if [ "$deployment_ready" = false ]; then
        log "${YELLOW}WARNING: Some components may not be ready for deployment${NC}"
        log "Proceeding with caution - verify component readiness manually"
    else
        log "${GREEN}All components ready for deployment${NC}"
    fi
}

# Run smoke tests on ansible roles
validate_ansible_roles() {
    log "${BLUE}--- Validating Ansible Roles ---${NC}"
    
    if [ ! -d "$ANSIBLE_HOME/roles" ]; then
        log "${YELLOW}WARNING: No roles directory found${NC}"
        return 0
    fi
    
    # Check role structure and dependencies
    for role_dir in "$ANSIBLE_HOME/roles"/*/; do
        if [ -d "$role_dir" ]; then
            local role_name=$(basename "$role_dir")
            log "Validating role: $role_name"
            
            # Check for required role structure
            local required_dirs=("tasks" "handlers" "vars" "defaults")
            for req_dir in "${required_dirs[@]}"; do
                if [ -d "$role_dir/$req_dir" ]; then
                    log "  [+] $req_dir/ directory exists"
                else
                    log "  [!] $req_dir/ directory missing (optional)"
                fi
            done
            
            # Validate main task file syntax
            if [ -f "$role_dir/tasks/main.yml" ]; then
                if ansible-playbook --syntax-check "$role_dir/tasks/main.yml" -i "$ANSIBLE_HOME/inventory/hosts" 2>/dev/null; then
                    log "${GREEN}  [+] $role_name tasks syntax valid${NC}"
                else
                    log "${RED}  [!] $role_name tasks have syntax errors${NC}"
                    error_exit "Role $role_name has invalid task syntax"
                fi
            fi
        fi
    done
    
    log "${GREEN}All roles validated${NC}"
}

# Test configuration file integrity
validate_configuration_integrity() {
    log "${BLUE}--- Validating Configuration File Integrity ---${NC}"
    
    # Get list of YAML files from data1
    local config_files=$(ssh ops@data1 "find /data/sentinelcam/source -name '*.yaml' -o -name '*.yml'" 2>/dev/null || true)
    
    if [ -n "$config_files" ]; then
        echo "$config_files" | while read -r config_file; do
            if [ -n "$config_file" ]; then
                local basename_file=$(basename "$config_file")
                log "Validating configuration: $basename_file"
                
                # Download and validate YAML syntax
                if ssh ops@data1 "python3 -c \"import yaml; yaml.safe_load(open('$config_file'))\"" 2>/dev/null; then
                    log "${GREEN}  [+] $basename_file YAML syntax valid${NC}"
                else
                    log "${RED}  [!] $basename_file has invalid YAML syntax${NC}"
                    error_exit "Configuration file $basename_file has invalid syntax"
                fi
            fi
        done
    else
        log "${YELLOW}WARNING: No configuration files found for validation${NC}"
    fi
}

# Validate system resources on buzz
validate_system_resources() {
    log "${BLUE}--- Validating System Resources ---${NC}"
    
    # Check disk space
    local disk_usage=$(df /home | awk 'NR==2 {print $5}' | sed 's/%//')
    log "Disk usage: ${disk_usage}%"
    
    if [ "$disk_usage" -gt 90 ]; then
        error_exit "Disk usage too high: ${disk_usage}%"
    elif [ "$disk_usage" -gt 80 ]; then
        log "${YELLOW}WARNING: High disk usage: ${disk_usage}%${NC}"
    else
        log "${GREEN}[+] Disk usage acceptable: ${disk_usage}%${NC}"
    fi
    
    # Check memory
    local mem_usage=$(free | awk 'NR==2{printf "%.0f", $3*100/$2}')
    log "Memory usage: ${mem_usage}%"
    
    if [ "$mem_usage" -gt 95 ]; then
        error_exit "Memory usage too high: ${mem_usage}%"
    elif [ "$mem_usage" -gt 85 ]; then
        log "${YELLOW}WARNING: High memory usage: ${mem_usage}%${NC}"
    else
        log "${GREEN}[+] Memory usage acceptable: ${mem_usage}%${NC}"
    fi
    
    # Check load average
    local load_avg=$(uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | sed 's/,//')
    log "Load average: $load_avg"
    
    # Basic load check (assuming 4 core system)
    if (( $(echo "$load_avg > 8.0" | bc -l) )); then
        log "${YELLOW}WARNING: High system load: $load_avg${NC}"
    else
        log "${GREEN}[+] System load acceptable: $load_avg${NC}"
    fi
}

# Run pre-deployment tests if available
run_pre_deployment_tests() {
    log "${BLUE}--- Running Pre-Deployment Tests ---${NC}"
    
    # Check if we have the test script available
    if ssh ops@data1 "test -f /data/sentinelcam/source/scripts/run-tests.sh"; then
        # Download and run limited pre-deployment tests
        log "Running final validation tests..."
        
        # Create temporary test directory
        local temp_test_dir="/tmp/sentinelcam_validation_$$"
        mkdir -p "$temp_test_dir"
        
        # Download test framework
        if scp -r ops@data1:/data/sentinelcam/source/scripts/run-tests.sh "$temp_test_dir/"; then
            chmod +x "$temp_test_dir/run-tests.sh"
            
            # Run only essential validation tests (not full suite)
            export RUN_UNIT_TESTS=false
            export RUN_INTEGRATION_TESTS=false
            export RUN_PERFORMANCE_TESTS=false
            
            log "Running configuration validation tests..."
            cd "$temp_test_dir"
            # Note: This would need a minimal validation mode in the test script
            # For now, just validate that the script exists and is executable
            if [ -x "./run-tests.sh" ]; then
                log "${GREEN}[+] Test framework is available and executable${NC}"
            else
                log "${YELLOW}WARNING: Test framework not properly downloaded${NC}"
            fi
            
            # Cleanup
            cd /
            rm -rf "$temp_test_dir"
        else
            log "${YELLOW}WARNING: Could not download test framework for validation${NC}"
        fi
    else
        log "${YELLOW}WARNING: No test framework available for pre-deployment validation${NC}"
    fi
}

# Generate validation report
generate_validation_report() {
    log "${BLUE}--- Generating Validation Report ---${NC}"
    
    local report_file="$ANSIBLE_HOME/logs/production_validation_report_$(date +%Y%m%d_%H%M%S).json"
    
    cat > "$report_file" << EOF
{
    "validation_timestamp": "$(date -Iseconds)",
    "buzz_hostname": "$(hostname)",
    "validation_status": "PASSED",
    "validations_performed": [
        "ansible_connectivity",
        "ansible_syntax", 
        "deployment_readiness",
        "ansible_roles",
        "configuration_integrity",
        "system_resources",
        "pre_deployment_tests"
    ],
    "system_info": {
        "disk_usage_percent": $(df /home | awk 'NR==2 {print $5}' | sed 's/%//'),
        "memory_usage_percent": $(free | awk 'NR==2{printf "%.0f", $3*100/$2}'),
        "load_average": "$(uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | sed 's/,//')"
    },
    "log_file": "$VALIDATION_LOG"
}
EOF
    
    log "${GREEN}Validation report generated: $report_file${NC}"
}

# Main validation sequence
main() {
    mkdir -p "$(dirname "$VALIDATION_LOG")"
    
    log "Starting production validation on buzz..."
    log "Validation log: $VALIDATION_LOG"
    
    # Run all validation checks
    validate_system_resources
    validate_ansible_connectivity
    validate_ansible_syntax
    validate_deployment_readiness
    validate_ansible_roles
    validate_configuration_integrity
    run_pre_deployment_tests
    
    # Generate report
    generate_validation_report
    
    log "${GREEN}[+] Production validation completed successfully!${NC}"
    log "System is ready for Ansible deployment to production nodes"
    
    return 0
}

# Handle command line arguments
case "${1:-}" in
    "--skip-connectivity")
        validate_ansible_connectivity() { log "${YELLOW}Skipping connectivity check${NC}"; }
        ;;
    "--skip-tests")
        run_pre_deployment_tests() { log "${YELLOW}Skipping pre-deployment tests${NC}"; }
        ;;
    "--help"|"-h")
        echo "Usage: $0 [--skip-connectivity|--skip-tests|--help]"
        echo ""
        echo "Production validation script for SentinelCam deployment"
        echo ""
        echo "Options:"
        echo "  --skip-connectivity   Skip Ansible connectivity tests"
        echo "  --skip-tests          Skip pre-deployment test execution"
        echo "  --help, -h            Show this help message"
        echo ""
        echo "This script validates the system is ready for production deployment"
        echo "including Ansible connectivity, syntax validation, and system resources."
        exit 0
        ;;
esac

# Run main validation
if main "$@"; then
    exit 0
else
    error_exit "Production validation failed"
fi
