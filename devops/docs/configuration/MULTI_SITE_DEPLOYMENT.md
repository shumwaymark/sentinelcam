# Multi-Site Deployment Guide

## Overview

SentinelCam's Ansible infrastructure supports deployment to multiple sites with different
hostnames and network configurations. Each site gets its own inventory file with site-specific
variables, and the bastion role generates DNS entries dynamically from that inventory.

## How It Works

### Site Configuration Variables

Each site defines its identity in the inventory file's `all.vars` section (or in
`group_vars/all/site.yaml`):

- `sentinelcam_site_name`: Identifier for the site (e.g., "chandler", "remote")
- `sentinelcam_bastion_hostname`: Must match the bastion's `inventory_hostname`
- `sentinelcam_health_check_hosts`: Reference hosts for connectivity tests

See `SITE_VARIABLES_REFERENCE.md` for the complete variable list.

### Dynamic DNS Generation

The bastion role generates DNS entries from the Ansible inventory rather than hardcoded
host lists:

```yaml
bastion_dnsmasq:
  generate_from_inventory: true
  inventory_groups:
    - sentinelcam_nodes
  extra_static_hosts:
    - { name: "gateway", ip: "192.168.10.254" }
```

The `sentinelcam.conf.j2` template iterates through the specified inventory groups, creates
an `address=/<hostname>/<ip>` entry for each host, adds reverse PTR records, and appends any
extra static hosts. Each entry is commented with the node role.

### Host Assertions

The bastion deployment playbook validates it's running on the correct host:

```yaml
- inventory_hostname == sentinelcam_bastion_hostname
- "'infrastructure' in group_names"
```

This prevents accidental deployment of bastion configuration to the wrong node.

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

See `inventory/group_vars/infrastructure/vault.yaml.template` for the vault file structure.

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
