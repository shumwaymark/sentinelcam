# Multi-Site Deployment Guide

## Overview

The SentinelCam Ansible infrastructure now supports deployment to multiple sites with different hostnames and network configurations. This is achieved through site-specific variable files and dynamic DNS generation from inventory.

## Key Changes

### 1. Site Configuration Variables

**File:** `inventory/group_vars/all/site.yaml`

This file defines site-specific settings:
- `sentinelcam_site_name`: Identifier for the site (e.g., "chandler", "remote")
- `sentinelcam_site_location`: Human-readable location
- `sentinelcam_bastion_hostname`: The inventory hostname of the bastion host
- `sentinelcam_health_check_hosts`: Reference hosts for connectivity tests
- `sentinelcam_vpn_gateway`: VPN gateway IP for health checks

### 2. Dynamic DNS Generation

**File:** `roles/bastion/defaults/main.yaml`

DNS entries are now dynamically generated from the Ansible inventory:

```yaml
bastion_dnsmasq:
  generate_from_inventory: true
  inventory_groups:
    - sentinelcam_nodes
  extra_static_hosts:
    - { name: "gateway", ip: "192.168.10.254" }
```

**Benefits:**
- No hardcoded hostnames in the role
- DNS automatically includes all hosts from inventory
- Easy to add site-specific devices via `extra_static_hosts`

### 3. Flexible Host Assertions

**File:** `playbooks/deploy-bastion.yaml`

The bastion deployment playbook now checks:
```yaml
- inventory_hostname == sentinelcam_bastion_hostname
- "'infrastructure' in group_names"
```

This validates the correct host type rather than a hardcoded hostname.

### 4. Variable-Based Health Checks

Health check targets now reference inventory hosts dynamically:
```yaml
bastion_health_checks:
  tests:
    - name: "internal_network"
      target: "{{ groups['datasinks'][0] | default('192.168.10.50') }}"
```

## Deploying to Multiple Sites

### Site 1: Original "Chandler" Site

**Inventory:** `inventory/production.yaml`

**Site config in inventory vars section:**
```yaml
all:
  vars:
    sentinelcam_site_name: "chandler"
    sentinelcam_bastion_hostname: "chandler-gate"
    sentinelcam_health_check_hosts:
      internal_network: "data1"
      dns_test: "data1"
```

**Deploy:**
```bash
ansible-playbook -i inventory/production.yaml playbooks/deploy-bastion.yaml
```

### Site 2: New Remote Site

**Inventory:** `inventory/site2-example.yaml` (rename and customize)

**Site config in inventory vars section:**
```yaml
all:
  vars:
    sentinelcam_site_name: "remote"
    sentinelcam_bastion_hostname: "remote-gate"
    sentinelcam_health_check_hosts:
      internal_network: "datasink2"
      dns_test: "datasink2"
```

**Deploy:**
```bash
ansible-playbook -i inventory/site2.yaml playbooks/deploy-bastion.yaml
```

## Migration from Hardcoded Values

### What Changed

1. **Playbook hostname check:**
   - Old: `inventory_hostname == "chandler-gate"`
   - New: `inventory_hostname == sentinelcam_bastion_hostname`

2. **DNS static hosts:**
   - Old: Hardcoded list in `roles/bastion/defaults/main.yaml`
   - New: Generated from `groups['sentinelcam_nodes']` in inventory

3. **Health check targets:**
   - Old: `ping -c 2 192.168.10.50`
   - New: `ping -c 2 {{ hostvars[sentinelcam_health_check_hosts.internal_network]['ansible_host'] }}`

4. **DNS test:**
   - Old: `nslookup data1 127.0.0.1`
   - New: `nslookup {{ sentinelcam_health_check_hosts.dns_test }} 127.0.0.1`

### Backward Compatibility

All existing deployments remain functional. The `site.yaml` file provides defaults for the Chandler site, so no changes are required to existing playbook runs.

## Creating a New Site

### Step 1: Create Site Inventory

Copy `inventory/site2-example.yaml` to `inventory/<sitename>.yaml` and customize:

1. Update hostnames in the `hosts:` sections
2. Update IP addresses in `ansible_host:` fields
3. Update network configuration in `vars:` section
4. Set `sentinelcam_site_name`, `sentinelcam_bastion_hostname`, etc.

### Step 2: Set Up Vault File (Required for WireGuard)

If the site uses WireGuard VPN, create and encrypt a vault file:

```bash
# Option A: Site-specific vault in inventory
mkdir -p inventory/<sitename>/group_vars/infrastructure
cp group_vars/infrastructure/vault.yaml.template \
   inventory/<sitename>/group_vars/infrastructure/vault.yaml
   
# Edit with site-specific secrets
ansible-vault edit inventory/<sitename>/group_vars/infrastructure/vault.yaml

# Option B: Use main vault file (if secrets are the same)
# Just ensure group_vars/infrastructure/vault.yaml exists and is encrypted
```

See `ANSIBLE_VAULT_SETUP.md` for detailed vault file configuration.

### Step 3: Update Site-Specific Variables (Optional)

If the site needs different bastion configuration (interfaces, networks, etc.), create:
- `inventory/group_vars/<sitename>/bastion.yaml`

Override any role defaults needed, for example:
```yaml
---
# Site-specific bastion configuration
bastion_interfaces:
  external:
    name: eth0
    ip: 192.168.50.254/24  # Different network
    gateway: 192.168.50.1
  internal:
    name: eth1
    ip: 192.168.60.254/24  # Different internal network

bastion_dnsmasq:
  dhcp_range: "192.168.60.11,192.168.60.250,24h"
  extra_static_hosts:
    - { name: "printer", ip: "192.168.60.100" }
    - { name: "nvr", ip: "192.168.60.101" }
```

### Step 4: Deploy

```bash
# With vault password prompt
ansible-playbook -i inventory/<sitename>.yaml playbooks/deploy-bastion.yaml --ask-vault-pass

# Or with password file
ansible-playbook -i inventory/<sitename>.yaml playbooks/deploy-bastion.yaml \
  --vault-password-file ~/.ansible_vault_pass
```

## Advanced: Environment-Specific Overrides

### Per-Environment Vault Files

Store site-specific secrets in separate vault files:

```bash
ansible-playbook -i inventory/site2.yaml \
  -e @group_vars/site2/vault.yml \
  --ask-vault-pass \
  playbooks/deploy-bastion.yaml
```

### Using --limit with Shared Inventory

If you maintain all sites in one inventory:

```bash
ansible-playbook -i inventory/all-sites.yaml \
  --limit remote-gate \
  playbooks/deploy-bastion.yaml
```

## DNS Template Behavior

The `sentinelcam.conf.j2` template now:

1. **Iterates through inventory groups** specified in `bastion_dnsmasq.inventory_groups`
2. **Creates DNS entries** for each host: `address=/<hostname>/<ansible_host>`
3. **Adds reverse PTR records** automatically
4. **Includes extra static hosts** from `bastion_dnsmasq.extra_static_hosts`
5. **Comments each entry** with the node role for clarity

Example generated DNS config:
```
# east (outpost)
address=/east/192.168.10.21

# data1 (datasink)
address=/data1/192.168.10.50

# gateway (extra)
address=/gateway/192.168.10.254
```

## Best Practices

### 1. Site Naming Convention
- Use lowercase, no spaces: `chandler`, `remote`, `site2`
- Keep names short but meaningful

### 2. Inventory Organization
```
inventory/
├── chandler/           # Site 1 (production)
│   └── hosts.yaml
├── remote/             # Site 2
│   └── hosts.yaml
└── group_vars/
    ├── all/
    │   └── common.yaml  # Shared across all sites
    ├── chandler/
    │   └── site.yaml    # Site-specific overrides
    └── remote/
        └── site.yaml
```

### 3. Testing New Sites
Always test with `--check` first:
```bash
ansible-playbook -i inventory/site2.yaml \
  --check \
  playbooks/deploy-bastion.yaml
```

### 4. Documentation
Maintain a site registry documenting:
- Site name and location
- Network ranges
- Bastion hostname
- Special configuration notes

## Troubleshooting

### Issue: "Playbook should only run on bastion host"

**Cause:** `sentinelcam_bastion_hostname` doesn't match `inventory_hostname`

**Fix:** Check your inventory and ensure:
```yaml
infrastructure:
  hosts:
    <bastion-hostname>:  # Must match sentinelcam_bastion_hostname
      ...
vars:
  sentinelcam_bastion_hostname: "<bastion-hostname>"
```

### Issue: DNS not resolving inventory hosts

**Cause:** Inventory group name mismatch

**Fix:** Ensure your inventory has the `sentinelcam_nodes` group:
```yaml
all:
  children:
    sentinelcam_nodes:
      children:
        modern_nodes:
          hosts:
            ...
```

Or override the group list:
```yaml
bastion_dnsmasq:
  inventory_groups:
    - your_custom_group
```

### Issue: Health checks failing

**Cause:** Reference host doesn't exist in inventory

**Fix:** Update `sentinelcam_health_check_hosts` to use valid hostnames from your inventory:
```yaml
sentinelcam_health_check_hosts:
  internal_network: "datasink2"  # Must exist in inventory
  dns_test: "datasink2"
```

## Migration Checklist

When migrating an existing site to this new structure:

- [ ] Create `group_vars/all/site.yaml` with current site settings
- [ ] Verify inventory includes all hosts in `sentinelcam_nodes` group
- [ ] Test DNS generation: `--tags dnsmasq --check`
- [ ] Run full deployment with `--check` flag
- [ ] Deploy to production
- [ ] Verify DNS resolution of all hosts
- [ ] Verify health checks pass
- [ ] Document any site-specific customizations

## Future Enhancements

Potential improvements for multi-site support:

1. **Site-specific firewall rules** - different allowed services per site
2. **Automatic backup sync** - replicate configs between sites
3. **Cross-site monitoring** - central dashboard for all sites
4. **Dynamic inventory** - pull site configs from central database
5. **Site templates** - `ansible-playbook create-site.yaml -e site_name=newsite`

## Summary

This refactoring provides:
- ✅ **Flexibility:** Deploy to unlimited sites with different hostnames
- ✅ **Maintainability:** Single source of truth (inventory) for all hosts
- ✅ **Safety:** Validation ensures deployment to correct hosts
- ✅ **Simplicity:** No role modifications needed for new sites
- ✅ **Backward Compatibility:** Existing deployments work unchanged
