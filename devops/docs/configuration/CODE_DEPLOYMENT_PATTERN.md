# SentinelCam Code Deployment - Extensible Pattern

## Overview

The SentinelCam Ansible deployment uses a **data-driven, extensible pattern** for code deployment. All deployment configuration is centralized in `inventory/group_vars/all/sentinelcam_standards.yaml`, making it easy to add new services or components without modifying role code.

## How It Works

### 1. Define Components in Standards

**File:** `inventory/group_vars/all/sentinelcam_standards.yaml`

```yaml
code_deployment:
  components:
    datasinks:
      - name: camwatcher
        source: "camwatcher/camwatcher"
        dest: "/home/{{ sentinelcam_user }}/camwatcher/camwatcher"
    
    outposts:
      - name: imagenode
        source: "imagenode"
        dest: "/home/{{ sentinelcam_user }}/imagenode"
        
    sentinels:
      - name: sentinel
        source: "sentinel"
        dest: "/home/{{ sentinelcam_user }}/sentinel"
```

### 2. Deployment Happens Automatically

When you run a playbook:

```bash
ansible-playbook playbooks/deploy-outpost.yaml
```

The `sentinelcam_base` role:
1. Identifies which groups the node belongs to
2. Looks up components for those groups
3. Deploys all matching components
4. Triggers appropriate service restarts

**No role-specific deployment code needed!**

## Adding a New Service

### Example: Add a new "reporter" service to datasinks

**Step 1:** Add to sentinelcam_standards.yaml

```yaml
code_deployment:
  components:
    datasinks:
      - name: camwatcher
        source: "camwatcher/camwatcher"
        dest: "/home/{{ sentinelcam_user }}/camwatcher/camwatcher"
      
      # NEW SERVICE - just add this entry!
      - name: reporter
        source: "reporter"
        dest: "/home/{{ sentinelcam_user }}/reporter"
```

**Step 2:** Create the role structure

```bash
roles/
  reporter/
    tasks/
      main.yaml      # Service config, templates
      deploy.yaml    # Post-deployment tasks (ownership, health checks)
    templates/
      reporter.service.j2
      reporter.yaml.j2
    handlers/
      main.yaml      # restart reporter handler
```

**Step 3:** Add to playbook

```yaml
# playbooks/deploy-reporter.yaml
- name: Deploy Reporter to data sinks
  hosts: datasinks
  become: yes
  roles:
    - sentinelcam_base    # Handles code deployment
    - reporter            # Handles service configuration
```

**That's it!** The code deployment is automatic.

## Adding Additional Directories

For services that need models, data files, or other ancillary directories:

```yaml
code_deployment:
  components:
    outposts:
      - name: imagenode
        source: "imagenode"
        dest: "/home/{{ sentinelcam_user }}/imagenode"
        additional_dirs:
          # Deploy ML models
          - source: "models/mobilenet_ssd"
            dest: "/home/{{ sentinelcam_user }}/imagenode/models/mobilenet"
          
          # Deploy configuration templates
          - source: "config/imagenode_defaults"
            dest: "/home/{{ sentinelcam_user }}/imagenode/config"
    
    sentinels:
      - name: sentinel
        source: "sentinel"
        dest: "/home/{{ sentinelcam_user }}/sentinel"
        additional_dirs:
          # Deploy OpenVINO models
          - source: "models/openvino/face_detection"
            dest: "/home/{{ sentinelcam_user }}/sentinel/models/face_detection"
          
          # Deploy test data
          - source: "data/test_images"
            dest: "/home/{{ sentinelcam_user }}/sentinel/test_data"
```

The `sentinelcam_base` role automatically syncs all `additional_dirs` alongside the main code.

## Deployment Source Structure

On the **primary datasink** (`data1`), code is staged in:

```
/home/ops/sentinelcam/current_deployment/
├── camwatcher/
│   ├── camwatcher/           # CamWatcher Python package
│   └── datapump/             # DataPump Python package
├── imagehub/                 # ImageHub code
├── imagenode/                # ImageNode code
├── sentinel/                 # Sentinel code
├── watchtower/               # Watchtower code
├── models/                   # Shared models (if using additional_dirs)
│   ├── mobilenet_ssd/
│   └── openvino/
├── data/                     # Shared data files
└── requirements.txt          # Python dependencies
```

## Advanced: Group-Specific Components

Components are deployed based on **group membership**:

```yaml
# inventory/hosts.yaml
all:
  children:
    datasinks:
      hosts:
        data1:      # Gets: camwatcher, datapump, imagehub
        data2:      # Gets: camwatcher, datapump, imagehub
    
    outposts:
      hosts:
        alpha5:     # Gets: imagenode
        lab1:       # Gets: imagenode
    
    sentinels:
      hosts:
        sentinel:   # Gets: sentinel
```

A node in **multiple groups** gets components from all groups:

```yaml
# Example: A node that's both datasink and sentinel
datasinks:
  hosts:
    data1:
    
sentinels:
  hosts:
    data1:  # Would get: camwatcher, datapump, imagehub, sentinel
```

## Deployment Tags

Control what gets deployed:

```bash
# Deploy everything (Python code, configs, services)
ansible-playbook playbooks/deploy-outpost.yaml

# Deploy code only (skip service config)
ansible-playbook playbooks/deploy-outpost.yaml --tags code_deployment

# Deploy additional directories only (models, data)
ansible-playbook playbooks/deploy-outpost.yaml --tags additional_dirs

# Skip code deployment (config/service changes only)
ansible-playbook playbooks/deploy-outpost.yaml --skip-tags code_deployment
```

## Benefits of This Pattern

✅ **Add services without touching role code** - just update standards.yaml  
✅ **Consistent deployment** - same logic for all services  
✅ **Easy to maintain** - one place to change deployment behavior  
✅ **Self-documenting** - standards.yaml shows all deployments  
✅ **Extensible** - additional_dirs for ancillary files  
✅ **Testable** - deployment strategy displayed before execution  

## Example: Adding a "Librarian" Service

```yaml
# 1. Add to sentinelcam_standards.yaml
code_deployment:
  components:
    datasinks:
      - name: librarian
        source: "librarian-prototype"
        dest: "/home/{{ sentinelcam_user }}/librarian"
        additional_dirs:
          - source: "config/gmail_credentials"
            dest: "/home/{{ sentinelcam_user }}/librarian/credentials"

# 2. Create playbook
# playbooks/deploy-librarian.yaml
- name: Deploy Librarian
  hosts: datasinks
  become: yes
  roles:
    - sentinelcam_base    # Auto-deploys librarian code
    - librarian           # Configures service

# 3. Deploy
# ansible-playbook playbooks/deploy-librarian.yaml
```

**No changes needed to sentinelcam_base role!**

## Troubleshooting

### See what would be deployed

```bash
ansible-playbook playbooks/deploy-outpost.yaml --check -vv
```

### Check component resolution

```bash
ansible alpha5 -m debug -a "var=components_to_deploy"
```

### Verify deployment strategy

The deployment tasks display strategy before execution:
- Target node
- Groups
- Components to deploy
- Source/destination paths

## Migration Path

If you have **existing deployment tasks** in roles:

1. ✅ Keep service management (start/stop/health checks) in role `deploy.yaml`
2. ✅ Remove code `synchronize`/`copy` tasks from roles
3. ✅ Add component definition to `sentinelcam_standards.yaml`
4. ✅ Let `sentinelcam_base` handle code deployment

**Result:** Cleaner, more maintainable deployment system!
