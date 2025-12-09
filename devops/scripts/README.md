# DevOps Scripts Directory

This directory contains shell scripts and utilities that support SentinelCam DevOps operations.

## Directory Structure

```
scripts/
├── deployment/                        # Deployment pipeline scripts
│   ├── production-validation.sh
│   ├── promote-to-production.sh
│   ├── rollback-to-last-stable.sh
│   └── deployment-health-monitor.sh
├── network/                           # Network and connectivity scripts
│   ├── validate_network.sh
│   ├── accept_ssh_host_keys.sh
│   └── clear_ssh_known_hosts.sh
├── sync/                              # Data synchronization scripts
│   ├── deploy.py
│   ├── deployment-config.yaml
│   ├── process-code-upload.sh
│   ├── integrate-code-update.sh
│   └── sync-ramrod-from-datasink.sh
└── utilities/                         # General DevOps utilities
    ├── add-new-node.sh
    └── replace_node.sh
```

## CI/CD Pipeline Scripts

The SentinelCam deployment pipeline uses these core scripts:

### **Active Pipeline Scripts**
- `sync/deploy.py` - Produce deployment package and upload to drop site on the bastion host
- `sync/process-code-upload.sh` - Process code uploads on bastion, forward to datasink
- `sync/integrate-code-update.sh` - Integrate code on datasink, create deployment flags
- `sync/sync-ramrod-from-datasink.sh` - Sync ansible from datasink to ramrod, execute deployments
