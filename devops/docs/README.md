# SentinelCam DevOps Documentation

**Complete documentation for deploying and managing a SentinelCam installation.**

---

## Documentation Structure

### [Getting Started](./getting-started/)

**New to SentinelCam or Ansible?** Start here.

- **[Ansible Beginner Guide](./getting-started/ANSIBLE_BEGINNER_GUIDE.md)** - Learn Ansible basics and safe practice techniques
- **[Adding a New Node](./getting-started/ADD_NEW_NODE.md)** - Step-by-step guide to provision a new Raspberry Pi node

### [Deployment](./deployment/)

**Ready to deploy?** Component deployment guides and workflows.

- **[Deployment Overview](./deployment/DEPLOYMENT_OVERVIEW.md)** - Comprehensive deployment guide for all components
  - Outpost (imagenode) deployment
  - Datasink services (camwatcher, datapump, imagehub)
  - Sentinel AI processing
  - Watchtower monitoring
  - Role README cross-references

### [Configuration](./configuration/)

**Configure your deployment** for single or multi-site operation.

- **[Outpost Registry Pattern](./configuration/OUTPOST_REGISTRY_PATTERN.md)** - Centralized outpost configuration architecture
- **[Code Deployment Pattern](./configuration/CODE_DEPLOYMENT_PATTERN.md)** - Understand code vs config deployment strategies
- **[Site Variables Reference](./configuration/SITE_VARIABLES_REFERENCE.md)** - Complete guide to site-specific configuration variables
- **[Multi-Site Deployment](./configuration/MULTI_SITE_DEPLOYMENT.md)** - Deploy to multiple physical sites
- **[Jetson Nano Setup](./configuration/JETSON_NANO_SETUP.md)** - Configure Jetson Nano for ML training (deepthink node)

### [Network](./network/)

**Network design and IP addressing** for isolated network operation.

- **[Network Addressing Standard](./network/NETWORK_ADDRESSING_STANDARD.md)** - Network architecture principles and design
- **[Network Addressing Plan](./network/NETWORK_ADDRESSING_PLAN.md)** - Current IP assignments and network map

### [Operations](./operations/)

**Day-to-day operations** and troubleshooting.

- **[Validation Checklist](./operations/VALIDATION_CHECKLIST.md)** - Pre-deployment validation steps
- **[ML Training Pipeline](./operations/ML_TRAINING_PIPELINE.md)** - Machine learning model training and deployment workflow

---

## Quick Navigation by Task

### First Time Setup
1. Read [Ansible Beginner Guide](./getting-started/ANSIBLE_BEGINNER_GUIDE.md)
2. Review [Network Addressing Standard](./network/NETWORK_ADDRESSING_STANDARD.md)
3. Follow [Adding a New Node](./getting-started/ADD_NEW_NODE.md)

### Deploying Components
1. Check [Deployment Overview](./deployment/DEPLOYMENT_OVERVIEW.md)
2. Reference specific role README in `../ansible/roles/<role>/README.md`
3. Use [Validation Checklist](./operations/VALIDATION_CHECKLIST.md) before deployment

### Multi-Site Deployment
1. Read [Multi-Site Deployment](./configuration/MULTI_SITE_DEPLOYMENT.md)
2. Configure using [Site Variables Reference](./configuration/SITE_VARIABLES_REFERENCE.md)
3. Follow [Network Addressing Plan](./network/NETWORK_ADDRESSING_PLAN.md)

### Troubleshooting
1. Review [Validation Checklist](./operations/VALIDATION_CHECKLIST.md)
2. Check component-specific role README in `../ansible/roles/<role>/README.md`
3. Review [Site Variables Reference](./configuration/SITE_VARIABLES_REFERENCE.md) for configuration issues

### Understanding Code Deployment
1. Read [Code Deployment Pattern](./configuration/CODE_DEPLOYMENT_PATTERN.md)
2. See [Deployment Overview](./deployment/DEPLOYMENT_OVERVIEW.md) workflows
3. Check CI/CD pipeline details in `../README.rst`

---

## Role-Specific Documentation

Detailed component deployment documentation is maintained in role READMEs:

| Component | Role README | Purpose |
|-----------|-------------|---------|
| **ImageNode** | [roles/imagenode/README.md](../ansible/roles/imagenode/README.md) | Outpost camera nodes |
| **CamWatcher** | [roles/camwatcher/README.md](../ansible/roles/camwatcher/README.md) | Event monitoring (datasink) |
| **DataPump** | [roles/datapump/README.md](../ansible/roles/datapump/README.md) | Data retrieval (datasink) |
| **ImageHub** | [roles/imagehub/README.md](../ansible/roles/imagehub/README.md) | Image aggregation (datasink) |
| **Sentinel** | [roles/sentinel/README.md](../ansible/roles/sentinel/README.md) | AI processing |
| **Watchtower** | [roles/watchtower/README.md](../ansible/roles/watchtower/README.md) | Live view display |
| **Base Provisioning** | [roles/sentinelcam_base/README.md](../ansible/roles/sentinelcam_base/README.md) | Foundation for all nodes |
| **Bastion** | [roles/bastion/README.md](../ansible/roles/bastion/README.md) | Network gateway/VPN |

---

## Additional Resources

### In This Repository
- **[DevOps Overview](../README.rst)** - CI/CD pipeline architecture and philosophy
- **[Ansible Guide](../ansible/README.md)** - Ansible deployment quick start and patterns
- **[Scripts Documentation](../scripts/README.md)** - Utility scripts reference

### Key Patterns & Standards
- **[Outpost Registry Pattern](./configuration/OUTPOST_REGISTRY_PATTERN.md)** - Centralized outpost configuration
- **[Network Addressing Standard](./network/NETWORK_ADDRESSING_STANDARD.md)** - IP allocation strategy
- **[Code Deployment Pattern](./configuration/CODE_DEPLOYMENT_PATTERN.md)** - Deployment architecture

### Development & Project Info
- **[Project README](../../README.rst)** - SentinelCam project overview
- **[History](../../HISTORY.md)** - Project development history
- **[License](../../LICENSE)** - MIT License

---

## Documentation Maintenance

**Last Updated:** December 7, 2025  
**Status:** Current (12 documents across 5 folders)

---

## Need Help?

**Can't find what you're looking for?**

1. Check the [Quick Navigation](#-quick-navigation-by-task) section above
2. Search role READMEs in `../ansible/roles/*/README.md`
3. Review [Troubleshooting Variables](./operations/TROUBLESHOOTING_VARIABLES.md)
4. Check [Ansible Guide](../ansible/README.md) for deployment commands

**For component-specific issues:**
- Refer to the [Role-Specific Documentation](#-role-specific-documentation) table
- Each role README contains troubleshooting sections
