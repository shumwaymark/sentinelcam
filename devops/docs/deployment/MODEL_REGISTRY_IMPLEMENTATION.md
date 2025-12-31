# Model Registry Implementation Summary

**Date**: 2025-12-08  
**Status**: Implementation Complete - Ready for Initial Deployment

## Overview

Implemented comprehensive model management system for SentinelCam with:
- Centralized model registry with versioned storage
- Configuration-driven deployment (models stay deployed, configs reference versions)
- Automated ML pipeline integration
- Rollback capability
- Retention management
- Multi-site sync documentation (for future)

## What Was Implemented

### 1. Core Configuration Files

#### `devops/ansible/inventory/group_vars/all/model_registry.yaml` ✅
- Central registry defining current/previous versions for all models
- Models: mobilenet_ssd, face_detection, face_recognition, haarcascades, openface_torch
- YYYY-MM-DD timestamp versioning
- Previous version tracking for rollback

#### `devops/ansible/inventory/group_vars/all/sentinelcam_standards.yaml` ✅
- Added `sentinelcam_model_registry` path variable
- Added `sentinelcam_model_retention` policy (default: 5)

#### `devops/ansible/inventory/group_vars/outposts/models.yaml` ✅
- Default model list for outpost nodes: `[mobilenet_ssd]`
- Can be overridden per-host in host_vars

### 2. Sentinel Task Templates (6 files) ✅

Created Jinja2 templates in `devops/ansible/roles/sentinel/templates/tasks/`:
- `GetFaces.yaml.j2` - Face detection task
- `FaceRecon.yaml.j2` - Face recognition task
- `FaceDataUpdate.yaml.j2` - Face data update task
- `FaceSweep.yaml.j2` - Face sweep task
- `MobileNetSSD_allFrames.yaml.j2` - Object detection task
- `DailyCleanup.yaml.j2` - Cleanup task (no models)

**Features**:
- Model paths injected from `sentinelcam_models` registry
- Support for per-host `model_version_overrides`
- Fallback to `current_version` if no override

### 3. Updated Sentinel Role ✅

#### `devops/ansible/roles/sentinel/tasks/main.yaml`
- Added task directory creation
- Added task template deployment loop
- Triggers sentinel restart on task config changes

#### `devops/ansible/roles/sentinel/handlers/main.yaml`
- Added `restart sentinel` handler

#### `devops/ansible/roles/sentinel/defaults/main.yaml`
- Added `model_version_overrides: {}` default

### 4. Updated ImageNode Role ✅

#### `devops/ansible/roles/imagenode/defaults/main.yaml`
- Updated `mobilenet_base_path` to use versioned path
- Added `imagenode_models_path` variable
- Added `model_version_overrides: {}` default
- Maintained backward compatibility with existing templates

### 5. Model Deployment Playbook ✅

#### `devops/ansible/playbooks/deploy-models.yaml`
- Pre-deployment validation (registry and version checks)
- Separate plays for sentinels, outposts, datasinks
- Uses rsync with `--update --checksum` for integrity
- Filters outpost models by `outpost_models` list
- Handles face_recognition linked files (facelist.csv, facedata.hdf5)
- Tag support for selective deployment

**Usage**:
```bash
# Deploy all models
ansible-playbook playbooks/deploy-models.yaml

# Deploy specific model
ansible-playbook playbooks/deploy-models.yaml --tags=face_recognition

# Deploy to specific host
ansible-playbook playbooks/deploy-models.yaml --limit=sentinel1
```

### 6. DataPump Facelist Coordination ✅

#### `devops/ansible/roles/datapump/defaults/main.yaml`
- Updated `faces_data_file` to reference versioned facelist
- Path: `{{ sentinelcam_data_path }}/face_recognition/facelist_{{ version }}.csv`

#### `devops/ansible/roles/datapump/handlers/main.yaml`
- Added `restart datapump` handler

**Effect**: DataPump automatically uses correct facelist version when face_recognition model changes

### 7. DeepThink Upload Script ✅

#### `devops/ansible/roles/deepthink/templates/upload_and_deploy.sh.j2`
- Auto-generates YAML manifest with SHA256 checksums
- Placeholder structure for training metrics (with TODO comments)
- Updates model_registry.yaml (current_version + previous_version)
- Uploads to registry on primary_datasink
- Triggers deploy-models.yaml on control node
- Comprehensive logging

**Usage** (on DeepThink node):
```bash
./upload_and_deploy.sh face_recognition 2025-12-08 \
    facemodel.pickle baselines.hdf5 facelist.csv facedata.hdf5
```

### 8. Model Retention Cleanup ✅

#### `devops/scripts/cleanup_model_registry.sh`
- Removes old versions while preserving:
  - current_version
  - previous_version
  - N most recent (configurable)
- Logs all operations to `model_cleanup.log`
- Reports disk space freed

#### `devops/ansible/roles/datasink/templates/model_cleanup.service.j2`
- Systemd oneshot service
- Security hardening (NoNewPrivileges, PrivateTmp, ProtectSystem)

#### `devops/ansible/roles/datasink/templates/model_cleanup.timer.j2`
- Runs weekly (Sunday 3:00 AM)
- 30-minute random delay to avoid load spikes
- Persistent across reboots

### 9. Rollback Utility Script ✅

#### `devops/scripts/rollback_model.sh`
- Interactive CLI with colored output
- List all models and versions
- Quick rollback to previous_version with `--previous` flag
- Validation checks before rollback
- Confirmation prompt
- Updates registry, commits to git, triggers config deployment
- Comprehensive logging

**Usage**:
```bash
# List all models
./rollback_model.sh

# List versions for specific model
./rollback_model.sh face_recognition

# Rollback to previous version
./rollback_model.sh face_recognition --previous

# Rollback to specific version
./rollback_model.sh face_recognition 2025-11-20
```

### 10. Multi-Site Sync Documentation ✅

#### `devops/docs/future/MULTI_SITE_MODEL_SYNC.md`
- Complete design document for future multi-site capability
- Push vs pull architectures
- Sync triggers and conflict resolution
- Bandwidth optimization strategies
- Configuration structure examples
- Implementation phases
- Security considerations
- Not implemented yet (pending requirements)

## Design Principles Applied

### 1. Outpost Registry Pattern
Mirrored the successful outpost registry pattern:
- **Central registry** (`model_registry.yaml`) as single source of truth
- **Indirection** - configs reference versions from registry, not hardcoded paths
- **Host-level overrides** - `model_version_overrides` in host_vars
- **Filtered deployment** - `outpost_models` list controls what deploys where

### 2. Configuration-Only Rollback
- Models deployed once and persist on nodes
- Version changes via config updates only
- Services restart to pick up new configs
- Much faster than redeploying large model files
- Previous version tracked for quick rollback

### 3. Version Management
- YYYY-MM-DD timestamp format (simple, sortable)
- No test/production distinction (use host overrides for experiments)
- Manifest per version with checksums
- previous_version field enables one-command rollback

### 4. Separation of Concerns
- **Models**: Registry-based, version-controlled, rarely change
- **Code**: Separate deployment, can change frequently
- **Configs**: Template-generated, tie models to services

### 5. ML Pipeline Integration
- DeepThink script auto-uploads trained models
- Generates manifest with checksums
- Updates registry atomically
- Triggers deployment automatically
- Placeholder for training metrics (future enhancement)

## Next Steps - Initial Deployment

### Step 1: Migrate Existing Models to Registry

On `primary_datasink`:
```bash
# Create registry structure
mkdir -p /home/ops/sentinelcam/model_registry/{mobilenet_ssd,face_detection,face_recognition,haarcascades,openface_torch}

# Migrate saved_models to versioned structure
# Assign historical dates to existing models
cd /home/ops/sentinelcam/saved_models

# Example for face_detection (adjust dates as appropriate)
mv opencv_dnn_face /home/ops/sentinelcam/model_registry/face_detection/2024-01-01

# Example for face_recognition (gamma3)
mv facemodel /home/ops/sentinelcam/model_registry/face_recognition/2025-11-20
cd /home/ops/sentinelcam/model_registry/face_recognition/2025-11-20
mv facelist_gamma3.csv facelist.csv
mv facedata_gamma3.hdf5 facedata.hdf5
mv baselines_gamma3.hdf5 baselines.hdf5
mv facemodel_gamma3.pickle facemodel.pickle

# Continue for other models...
```

### Step 2: Generate Manifests for Existing Models

For each version directory, create `manifest.yaml`:
```bash
cd /home/ops/sentinelcam/model_registry/face_detection/2024-01-01
cat > manifest.yaml << 'EOF'
%YAML 1.0
---
name: face_detection
version: "2024-01-01"
framework: caffe
source: opencv_dnn
date_deployed: "2024-01-01"

training_metrics:
  placeholder: true

files:
  - name: deploy.prototxt
    sha256: $(sha256sum deploy.prototxt | awk '{print $1}')
  - name: res10_300x300_ssd_iter_140000.caffemodel
    sha256: $(sha256sum res10_300x300_ssd_iter_140000.caffemodel | awk '{print $1}')

previous_version: null
EOF

# Actually compute checksums
for file in deploy.prototxt res10_300x300_ssd_iter_140000.caffemodel; do
    checksum=$(sha256sum "$file" | awk '{print $1}')
    sed -i "s/\$(sha256sum $file.*}/$checksum/" manifest.yaml
done
```

### Step 3: Verify Registry Configuration

Check `devops/ansible/inventory/group_vars/all/model_registry.yaml`:
- Ensure versions match migrated directories
- Verify previous_version is null for initial migration

### Step 4: Test Model Deployment

```bash
# From control node
cd /home/ops/sentinelcam/current_deployment/devops/ansible

# Test validation
ansible-playbook playbooks/deploy-models.yaml --check

# Deploy to single test node first
ansible-playbook playbooks/deploy-models.yaml --limit=test_sentinel

# Verify deployment
ssh sentinel1 'ls -la /home/ops/sentinel/models/*/2025-11-20/'

# Deploy to all nodes
ansible-playbook playbooks/deploy-models.yaml
```

### Step 5: Verify Services

```bash
# Check sentinel task configs were templated
ansible sentinels -a 'ls -la /home/ops/sentinel/tasks/'

# Restart services to pick up new configs
ansible sentinels -a 'systemctl restart sentinel'
ansible datasinks -a 'systemctl restart datapump'

# Verify services started
ansible sentinels -a 'systemctl status sentinel'
ansible datasinks -a 'systemctl status datapump'

# Check logs for errors
ansible sentinels -a 'journalctl -u sentinel -n 50'
```

### Step 6: Enable Cleanup Timer

```bash
# Deploy cleanup timer to primary_datasink
# (Update datasink role to include timer deployment)

# Manually test cleanup
ssh data1
/home/ops/sentinelcam/current_deployment/devops/scripts/cleanup_model_registry.sh

# Check cleanup log
cat /home/ops/sentinelcam/logs/model_cleanup.log
```

### Step 7: Test Rollback

```bash
# List available versions
/home/ops/sentinelcam/current_deployment/devops/scripts/rollback_model.sh face_recognition

# Test rollback to previous
/home/ops/sentinelcam/current_deployment/devops/scripts/rollback_model.sh face_recognition --previous

# Verify configs updated
grep -A2 face_recognition /home/ops/sentinelcam/current_deployment/devops/ansible/inventory/group_vars/all/model_registry.yaml

# Verify services restarted
ansible sentinels -a 'systemctl status sentinel'

# Test rollback to specific version
/home/ops/sentinelcam/current_deployment/devops/scripts/rollback_model.sh face_recognition 2025-11-20
```

### Step 8: Test DeepThink Upload (when ready)

```bash
# On DeepThink node after training
cd /home/ops/deepthink
./upload_and_deploy.sh face_recognition 2025-12-08 \
    facemodel.pickle baselines.hdf5 facelist.csv facedata.hdf5

# Verify model in registry
ssh data1 'ls -la /home/ops/sentinelcam/model_registry/face_recognition/2025-12-08/'

# Verify deployment triggered
ssh data1 'tail -100 /home/ops/sentinelcam/logs/model_deployment.log'
```

## Files Created/Modified

### New Files (23)
1. `devops/ansible/inventory/group_vars/all/model_registry.yaml`
2. `devops/ansible/inventory/group_vars/outposts/models.yaml`
3. `devops/ansible/roles/sentinel/templates/tasks/GetFaces.yaml.j2`
4. `devops/ansible/roles/sentinel/templates/tasks/FaceRecon.yaml.j2`
5. `devops/ansible/roles/sentinel/templates/tasks/FaceDataUpdate.yaml.j2`
6. `devops/ansible/roles/sentinel/templates/tasks/FaceSweep.yaml.j2`
7. `devops/ansible/roles/sentinel/templates/tasks/MobileNetSSD_allFrames.yaml.j2`
8. `devops/ansible/roles/sentinel/templates/tasks/DailyCleanup.yaml.j2`
9. `devops/ansible/playbooks/deploy-models.yaml`
10. `devops/ansible/roles/deepthink/templates/upload_and_deploy.sh.j2`
11. `devops/ansible/roles/datasink/templates/model_cleanup.service.j2`
12. `devops/ansible/roles/datasink/templates/model_cleanup.timer.j2`
13. `devops/scripts/cleanup_model_registry.sh`
14. `devops/scripts/rollback_model.sh`
15. `devops/docs/future/MULTI_SITE_MODEL_SYNC.md`
16. `devops/docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md` (this file)

### Modified Files (7)
1. `devops/ansible/inventory/group_vars/all/sentinelcam_standards.yaml` - Added registry paths
2. `devops/ansible/roles/sentinel/tasks/main.yaml` - Added task template deployment
3. `devops/ansible/roles/sentinel/handlers/main.yaml` - Added restart handler
4. `devops/ansible/roles/sentinel/defaults/main.yaml` - Added model_version_overrides
5. `devops/ansible/roles/imagenode/defaults/main.yaml` - Updated model paths
6. `devops/ansible/roles/datapump/defaults/main.yaml` - Updated facelist path
7. `devops/ansible/roles/datapump/handlers/main.yaml` - Added restart handler

## Testing Checklist

Before production deployment:

- [ ] Registry directory structure created
- [ ] Existing models migrated with correct versions
- [ ] Manifests generated for all model versions
- [ ] model_registry.yaml versions match actual directories
- [ ] Test deployment to single sentinel succeeds
- [ ] Test deployment to single outpost succeeds
- [ ] Task configs templated correctly
- [ ] Model paths resolve correctly in configs
- [ ] Services restart and start successfully
- [ ] Sentinel can load tasks and access models
- [ ] ImageNode can detect objects with models
- [ ] DataPump uses correct facelist
- [ ] Rollback script lists versions correctly
- [ ] Rollback updates configs and restarts services
- [ ] Cleanup script identifies removable versions
- [ ] All scripts are executable (chmod +x)

## Known Limitations

1. **No automatic metrics extraction**: Training metrics in manifest are placeholder
   - Requires notebook cell tagging and parsing (future enhancement)

2. **No pre-deployment model validation**: Models not tested before deployment
   - Consider adding smoke tests (future enhancement)

3. **Single-site only**: Multi-site sync not implemented
   - Design documented in MULTI_SITE_MODEL_SYNC.md

4. **Manual manifest generation**: Initial migration requires manual manifest creation
   - Could be automated with script (future enhancement)

5. **No model performance monitoring**: No runtime metrics collected
   - Consider adding inference time/accuracy tracking (future enhancement)

## Maintenance

### Regular Tasks
- **Weekly**: Cleanup timer runs automatically (Sunday 3 AM)
- **After training**: Run upload_and_deploy.sh
- **Before rollback**: Review logs and metrics

### Monitoring
- Check cleanup logs: `/home/ops/sentinelcam/logs/model_cleanup.log`
- Check deployment logs: `/home/ops/sentinelcam/logs/model_deployment.log`
- Monitor registry disk usage
- Verify service health after model updates

### Troubleshooting
- **Models not deploying**: Check deploy-models.yaml validation
- **Wrong model version**: Check model_registry.yaml and host_vars overrides
- **Service won't start**: Check task config paths and model file existence
- **Rollback fails**: Verify target version exists in registry

## Coral EdgeTPU Package Management

### Overview

Pre-built Coral/EdgeTPU packages are stored in the model registry for deployment to
nodes requiring hardware acceleration. Packages sourced from feranick's community ports:

- **libedgetpu**: https://github.com/feranick/libedgetpu
- **pycoral**: https://github.com/feranick/pycoral
- **tflite_runtime**: https://github.com/feranick/TFlite-builds

### Package Registry Structure

```
{{ sentinelcam_model_registry }}/coral_packages/
├── libedgetpu1-std_16.0tf2.17.1-1.bookworm_arm64.deb
├── libedgetpu-dev_16.0tf2.17.1-1.bookworm_arm64.deb
├── pycoral-2.0.3-cp311-cp311-linux_aarch64.whl
├── tflite_runtime-2.17.1-cp311-cp311-linux_aarch64.whl
└── manifest.yaml
```

### Configuration

Coral package versions are defined in `model_registry.yaml`:

```yaml
coral_packages:
  tf_version: "2.17.1"
  python_version: "3.11"
  
  libedgetpu:
    version: "16.0tf2.17.1-1"
    deb_package: "libedgetpu1-std_16.0tf2.17.1-1.bookworm_arm64.deb"
    
  pycoral:
    version: "2.0.3"
    wheel: "pycoral-2.0.3-cp311-cp311-linux_aarch64.whl"
    
  tflite_runtime:
    version: "2.17.1"
    wheel: "tflite_runtime-2.17.1-cp311-cp311-linux_aarch64.whl"
```

### Deployment

**Step 1: Download packages to datasink**
```bash
# Using Ansible (recommended)
ansible-playbook playbooks/deploy-coral-packages.yaml --tags=download

# Or manually
./devops/scripts/operations/download_coral_packages.sh
```

**Step 2: Deploy to nodes**
```bash
# Deploy to all coral-configured nodes
ansible-playbook playbooks/deploy-coral-packages.yaml

# Deploy to specific node type
ansible-playbook playbooks/deploy-coral-packages.yaml --limit=sentinels
ansible-playbook playbooks/deploy-coral-packages.yaml --limit=outposts
```

### Node Configuration

**Sentinel** (in host_vars or deploy-sentinel.yaml):
```yaml
sentinel_accelerator_type: coral  # [cpu, ncs2, coral]
```

**ImageNode** (in host_vars):
```yaml
imagenode_config:
  detector:
    accelerator: coral  # [none, ncs2, coral]
```

### Version Updates

When updating Coral package versions:

1. Update versions in `model_registry.yaml`
2. Run download playbook: `--tags=download`
3. Deploy to nodes: full playbook run
4. Services automatically restart to use new packages

### Role-Based Provisioning

Coral packages are automatically installed during role provisioning when configured:

**Sentinel Role** - Coral is the default accelerator for new deployments:
```yaml
# devops/ansible/roles/sentinel/defaults/main.yaml
sentinel_accelerator_type: coral  # [coral, cpu, ncs2]
sentinel_install_coral_packages: "{{ sentinel_accelerator_type == 'coral' }}"
```

**ImageNode Role** - Enable Coral in host_vars for outposts with hardware:
```yaml
# host_vars/my_outpost.yaml
imagenode_accelerator_type: coral
imagenode_install_coral_packages: true
```

The provisioning tasks (`coral_provisioning.yaml`) in each role will:
1. Check if packages are already installed
2. Sync packages from the datasink
3. Install udev rules for USB access
4. Install libedgetpu deb package
5. Install Python wheels into the venv
6. Verify installation

### Hardware Accelerator Migration Notes

**NCS2 (Intel Neural Compute Stick 2)** - DEPRECATED
- Intel discontinued support; no drivers for Raspberry Pi OS Bookworm/Python 3.11
- Legacy PyImageSearch OS image had OpenVINO pre-installed
- Existing NCS2 nodes will continue to work until hardware fails
- No new NCS2 deployments possible

**Coral EdgeTPU** - RECOMMENDED
- Thanks to feranick's community ports, Coral remains viable on modern OS
- Pre-built packages for arm64/Python 3.11 available on GitHub
- Now the default accelerator for new sentinel deployments
- Requires packages downloaded to datasink before provisioning

## Future Enhancements

1. **Training metrics extraction** - Parse Papermill notebook outputs
2. **Pre-deployment validation** - Test models before deployment
3. **Multi-site sync** - Implement hub-and-spoke sync pattern
4. **Model performance monitoring** - Track inference metrics
5. **Automated testing** - Unit tests for models post-deployment
6. **Web UI** - Visual model registry browser
7. **A/B testing** - Deploy experimental models to subset of nodes

## References

- Original design: `devops/docs/deployment/MODEL_DEPLOYMENT.md`
- Outpost registry pattern: `devops/docs/configuration/OUTPOST_REGISTRY_PATTERN.md`
- Multi-site design: `devops/docs/future/MULTI_SITE_MODEL_SYNC.md`
- Code deployment: `devops/ansible/roles/sentinelcam_base/tasks/code_deployment.yaml`
- Coral packages: `devops/ansible/playbooks/deploy-coral-packages.yaml`
- Coral provisioning: `devops/ansible/roles/sentinel/tasks/coral_provisioning.yaml`

---

**Implementation Complete**: 2025-12-08  
**Coral Support Added**: 2025-12-19  
**Ready for Initial Deployment**: Yes  
**Breaking Changes**: No (backward compatible)  
**Requires Manual Steps**: Yes (model migration, package download)
