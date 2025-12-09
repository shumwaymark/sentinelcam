# SentinelCam Component-Based Deployment Tool

A cross-platform Python CLI tool for selectively deploying SentinelCam components to the internal network via bastion host
providing selective component deployment, fast iteration, and full end-to-end automation.

## Features

- **Component-based deployment** - Deploy only what you need
- **Cross-platform** - Works on Windows, Linux, and macOS
- **Safe previews** - Dry-run mode prevents mistakes
- **Clear naming** - Packages show what's deployed
- **Smart filtering** - Automatic exclusion of test files, cache, etc.
- **Ansible integration** - Maps to existing component-specific playbooks
- **Maintainable** - YAML config instead of hardcoded paths

## Prerequisites

- Python 3.7+
- SSH access to bastion host (chandler-gate.local)
- SSH keys configured for passwordless authentication

## Quick Start

### List Available Components

```bash
python devops/scripts/sync/deploy.py --list
```

### Deploy Specific Components (Most Common)

```bash
# Update imagenode code
python devops/scripts/sync/deploy.py imagenode

# DevOps/Ansible updates
python devops/scripts/sync/deploy.py devops

# Update multiple services
python devops/scripts/sync/deploy.py sentinel watchtower
```

### Preview Before Deploying (Highly Recommended)

```bash
# Always safe - shows what would be deployed
python devops/scripts/sync/deploy.py --dry-run imagenode
python devops/scripts/sync/deploy.py --dry-run devops sentinel
```

### Deploy All Components (Rare)

```bash
python devops/scripts/sync/deploy.py --all
```

## Available Components

| Component | Use Case | Size | Ansible Playbook |
|-----------|----------|------|------------------|
| `imagenode` | Outpost camera nodes | ~280 KB | deploy-outpost.yaml |
| `imagehub` | Imagenode receiver | ~200 KB | deploy-imagehub.yaml |
| `camwatcher` | Outpost subscriber | ~350 KB | deploy-camwatcher.yaml |
| `datapump` | Data retrieval engine | ~100 KB | deploy-datapump.yaml |
| `sentinel` | Inference and ML pipeline | ~150 KB | deploy-sentinel.yaml |
| `watchtower` | Wall console, outpost and event veiwer | ~180 KB | deploy-watchtower.yaml |
| `devops` | Ansible playbooks/scripts | ~600 KB | - |

## Configuration

Configuration is stored in `deployment-config.yaml`:

```yaml
deployment:
  bastion_user: rocky
  bastion_host: chandler-gate.local
  transfer_cache: ~/transfer_cache/incoming
  processing_script: ~/scripts/process-code-upload.sh

components:
  - name: imagenode
    description: Outpost camera nodes
    paths:
      - imagenode/
    required_files:
      - imagenode/imagenode.py
    ansible_playbook: deploy-outpost.yaml
```

### Custom Configuration

Use a custom config file:

```bash
python deploy.py --config my-config.yaml imagenode
```

## Deployment Pipeline

The tool orchestrates the complete deployment pipeline:

1. **Package Creation**
   - Collects files for selected components
   - Applies inclusion/exclusion filters
   - Creates timestamped ZIP archive
   - Component tag in filename for easy identification

2. **Upload to Bastion**
   - Transfers package via SCP to chandler-gate.local
   - Places in `~/transfer_cache/incoming/`

3. **Trigger Processing**
   - Executes `process-code-upload.sh` on bastion
   - Bastion extracts and syncs to data1
   - data1 stages code for buzz

4. **Ansible Deployment**
   - Buzz pulls updates from data1
   - Runs appropriate Ansible playbooks
   - Deploys to target nodes

## Command-Line Options

```
usage: deploy.py [-h] [--all] [--list] [--dry-run] [--keep-package] 
                 [--config CONFIG] [components ...]

positional arguments:
  components         Components to deploy

optional arguments:
  -h, --help         Show help message
  --all              Deploy all components
  --list             List available components
  --dry-run          Preview without making changes
  --keep-package     Keep local ZIP after deployment
  --config CONFIG    Use custom configuration file
```

## Package Naming

Packages now show what's deployed with timestamps and component tags:

```
sentinelcam-imagenode-20251112_143022.zip
sentinelcam-sentinel-watchtower-20251112_143145.zip
sentinelcam-devops-config-20251112_143301.zip
sentinelcam-imagenode-sentinel-watchtower-devops-plus-20251112_143422.zip  # 4+ components
```

This makes it easy to identify what's in each package compared to the old generic naming.

## File Filtering

### Included Extensions

Python, shell scripts, YAML, JSON, text files, service definitions, Jinja2 templates, etc.

### Automatically Excluded

- Git metadata (`.git/`, `*.pyc`)
- Virtual environments (`.venv/`, `venv/`)
- IDE files (`.vscode/`, `.idea/`)
- Test results and caches
- Backup files (`*.bak`, `*-OLD.*`)
- Binary data and models
- Log files

### Component-Specific Exclusions

Each component can define additional exclusions in `deployment-config.yaml`.

## Monitoring Deployment

### Watch Bastion Logs

```bash
ssh rocky@chandler-gate.local 'tail -f ~/transfer_cache/logs/deployment.log'
```

### Check on Buzz

```bash
ssh ops@data1 'ls -l sentinelcam/current_deployment' # Check deployment timestamp
```

### Verify on Target Nodes

```bash
# From buzz
ansible outposts -m shell -a 'ls -lh /opt/sentinelcam/imagenode/imagenode.py'
```

## Troubleshooting

### SSH Connection Issues

```bash
# Test SSH access
ssh rocky@chandler-gate.local 'echo Connected'

# Check SSH keys
ssh-add -l
```

### Package Too Large

Deploy fewer components or check for included binary files:

```bash
# Use dry-run to see file list
python deploy.py --dry-run --all | grep -E '\.(mp4|jpg|png|h5|blob)'
```

### Component Not Found

```bash
# List available components
python deploy.py --list

# Check spelling - names are case-sensitive
```

### Deployment Hangs

The `process-code-upload.sh` script on bastion may be running. Check:

```bash
ssh rocky@chandler-gate.local 'ps aux | grep process-code'
ssh rocky@chandler-gate.local 'tail -20 ~/transfer_cache/logs/deployment.log'
```

## Advanced Usage

### Keep Package Locally

```bash
# Keep the ZIP file for inspection or manual deployment
python devops/scripts/sync/deploy.py --keep-package imagenode
```

### Custom Configuration

```bash
# Use different bastion or settings
python devops/scripts/sync/deploy.py --config staging-config.yaml imagenode
```

### Integration with CI/CD

```bash
#!/bin/bash
# In CI pipeline
python devops/scripts/sync/deploy.py --dry-run imagenode || exit 1
python devops/scripts/sync/deploy.py imagenode
```

## Need Help?

```bash
python devops/scripts/sync/deploy.py --help
python devops/scripts/sync/deploy.py --list
```

## License

Same as SentinelCam project.
