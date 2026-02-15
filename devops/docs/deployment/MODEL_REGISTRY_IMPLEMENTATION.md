# Model Registry

## Overview

The **Model Registry** centralizes ML model version management for SentinelCam. All models are stored
on the primary datasink in a versioned directory structure. A single configuration file
(`model_registry.yaml`) tracks the current version of every model. Roles reference the registry
to resolve model paths — changing a version is a config update and service restart, not a file
redeployment.

This mirrors the [Outpost Registry Pattern](../configuration/OUTPOST_REGISTRY_PATTERN.md): one
source of truth, indirection through configuration, host-level overrides where needed.

## Architecture

```
model_registry.yaml (group_vars/all/)     ← single source of truth for versions
        │
        ├─→ Sentinel task templates       ← model paths injected into task configs
        ├─→ ImageNode templates           ← detection model paths
        └─→ DataPump templates            ← facelist version reference

Primary Datasink filesystem:
  model_registry/
  ├── mobilenet_ssd/YYYY-MM-DD/           ← versioned model directories
  ├── face_detection/YYYY-MM-DD/
  ├── face_recognition/YYYY-MM-DD/
  ├── haarcascades/YYYY-MM-DD/
  ├── openface_torch/YYYY-MM-DD/
  └── coral_packages/                     ← EdgeTPU runtime packages
```

## Registry Configuration

**File:** `inventory/group_vars/all/model_registry.yaml`

Each model entry tracks `current_version` and `previous_version` (for rollback):

```yaml
sentinelcam_models:
  mobilenet_ssd:
    current_version: "2020-12-06"
    previous_version: null

  face_recognition:
    current_version: "2026-02-02"
    previous_version: "2025-02-25"
```

Version format is `YYYY-MM-DD`. Templates resolve model paths as:

```
{{ sentinelcam_model_registry }}/{{ model_name }}/{{ current_version }}/
```

### Per-Host Overrides

Set `model_version_overrides` in host_vars to pin a specific node to a different version:

```yaml
# host_vars/sentinel-test.yaml
model_version_overrides:
  face_recognition: "2025-11-20"
```

### Outpost Model Filtering

Outposts only receive models listed in their `outpost_models` variable. Default (from
`group_vars/outposts/models.yaml`): `[mobilenet_ssd]`. Override per-host for nodes
needing additional models.

## Deployment

```bash
# Deploy all models to all nodes
ansible-playbook playbooks/deploy-models.yaml

# Deploy specific model type
ansible-playbook playbooks/deploy-models.yaml --tags=face_recognition

# Deploy to one node
ansible-playbook playbooks/deploy-models.yaml --limit=<hostname>
```

The playbook validates that registry versions exist on the datasink before syncing. Uses
rsync with `--update --checksum` for integrity. Sentinel task configs are re-templated and
services restarted on version changes.

## Rollback

Since model files persist on nodes across versions, rollback is configuration-only:

```bash
# List models and versions
devops/scripts/rollback_model.sh

# Quick rollback to previous_version
devops/scripts/rollback_model.sh face_recognition --previous

# Rollback to specific version
devops/scripts/rollback_model.sh face_recognition 2025-11-20
```

The script updates `model_registry.yaml`, commits the change, and triggers config redeployment.

## Retention Cleanup

A systemd timer on the primary datasink runs weekly to remove old model versions. Preserves
`current_version`, `previous_version`, and the N most recent (configurable via
`sentinelcam_model_retention`, default: 5).

## Coral EdgeTPU Packages

Pre-built Coral runtime packages (from [feranick's community ports](https://github.com/feranick/libedgetpu))
are stored alongside models at `model_registry/coral_packages/`. Versions are defined in
the `coral_packages` section of `model_registry.yaml`.

```bash
# Download packages to datasink, then deploy to nodes
ansible-playbook playbooks/deploy-coral-packages.yaml
```

Roles install Coral packages automatically during provisioning when `sentinel_accelerator_type: coral`
or `imagenode_accelerator_type: coral` is set.

NCS2 (Intel Neural Compute Stick 2) is deprecated — no Bookworm/Python 3.11 drivers available.
Coral is the recommended accelerator for new deployments.

## Key Files

| File | Purpose |
|------|---------|
| `inventory/group_vars/all/model_registry.yaml` | Version registry (source of truth) |
| `inventory/group_vars/all/sentinelcam_standards.yaml` | Registry path variables |
| `inventory/group_vars/outposts/models.yaml` | Default outpost model list |
| `playbooks/deploy-models.yaml` | Model deployment playbook |
| `playbooks/deploy-coral-packages.yaml` | Coral package deployment |
| `roles/sentinel/templates/tasks/*.yaml.j2` | Sentinel task configs (model paths injected) |
| `devops/scripts/rollback_model.sh` | Version rollback utility |
| `devops/scripts/cleanup_model_registry.sh` | Retention cleanup |

## See Also

- [Outpost Registry Pattern](../configuration/OUTPOST_REGISTRY_PATTERN.md)
- [Code Deployment Pattern](../configuration/CODE_DEPLOYMENT_PATTERN.md)
- [Face Detection Model Selection](FACE_DETECTION_MODEL_SELECTION.md)
- [DeepThink Role](../../ansible/roles/deepthink/README.md) — ML training pipeline
