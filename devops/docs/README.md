# SentinelCam DevOps Documentation

Documentation for deploying and managing a SentinelCam installation.

## Getting Started

- [Adding a New Node](./getting-started/ADD_NEW_NODE.md) — Bootstrapping a Raspberry Pi from SD card to running node

## Configuration

- [Outpost Registry Pattern](./configuration/OUTPOST_REGISTRY_PATTERN.md) — Centralized outpost configuration architecture
- [Code Deployment Pattern](./configuration/CODE_DEPLOYMENT_PATTERN.md) — How code flows from development to nodes
- [Site Variables Reference](./configuration/SITE_VARIABLES_REFERENCE.md) — All site-specific Ansible variables
- [Multi-Site Deployment](./configuration/MULTI_SITE_DEPLOYMENT.md) — Deploying to multiple physical sites

## Deployment

- [Model Registry](./deployment/MODEL_REGISTRY_IMPLEMENTATION.md) — ML model version management and deployment
- [Face Detection Model Selection](./deployment/FACE_DETECTION_MODEL_SELECTION.md) — Per-host face detection model configuration

## Network

- [Network Addressing Standard](./network/NETWORK_ADDRESSING_STANDARD.md) — Network architecture and IP allocation strategy
- [Network Addressing Plan](./network/NETWORK_ADDRESSING_PLAN.md) — Current IP assignments and network map

## Role READMEs

Component-specific deployment details live in the role READMEs:

| Role | Purpose |
|------|---------|
| [sentinelcam_base](../ansible/roles/sentinelcam_base/README.md) | Foundation for all nodes |
| [imagenode](../ansible/roles/imagenode/README.md) | Outpost camera nodes |
| [imagehub](../ansible/roles/imagehub/README.md) | Image aggregation (datasink) |
| [camwatcher](../ansible/roles/camwatcher/README.md) | Event monitoring (datasink) |
| [datapump](../ansible/roles/datapump/README.md) | Data retrieval (datasink) |
| [sentinel](../ansible/roles/sentinel/README.md) | AI processing |
| [watchtower](../ansible/roles/watchtower/README.md) | Live view display |
| [bastion](../ansible/roles/bastion/README.md) | Network gateway / VPN |
| [infrastructure](../ansible/roles/infrastructure/README.md) | System-level bastion provisioning |
| [deepthink](../ansible/roles/deepthink/README.md) | ML training nodes |
| [ramrod](../ansible/roles/ramrod/README.md) | Ansible control node |
