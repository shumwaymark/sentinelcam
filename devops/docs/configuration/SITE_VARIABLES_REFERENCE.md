# Site Configuration Quick Reference

## Required Variables for Multi-Site Deployment

### Location: Inventory File `all.vars` section (Recommended)

**Best practice:** Define these in your inventory file under `all.vars` section.

**Example:** `inventory/production.yaml`
```yaml
all:
  children:
    # ... your groups and hosts
  vars:
    # Site identification
    sentinelcam_site_name: "chandler"          # Site identifier (lowercase, no spaces)
sentinelcam_site_location: "Ying Yang Ranch"  # Human-readable location name

# Bastion host configuration
sentinelcam_bastion_hostname: "chandler-gate"  # Must match inventory_hostname

# Health check reference hosts (must exist in inventory)
sentinelcam_health_check_hosts:
  internal_network: "data1"   # Host to ping for internal network test
  dns_test: "data1"           # Host to use for DNS resolution test

# VPN gateway (for VPN health checks)
sentinelcam_vpn_gateway: "10.0.0.1"

# Network interface names (hardware-specific)
bastion_interface_external: "eth0"          # External/WAN interface
bastion_interface_internal: "enp1s0u1u4"    # Internal/LAN interface
```

## Variable Reference by Function

### Hostname Validation

**Where used:** `playbooks/deploy-bastion.yaml`, `playbooks/configure_dns.yaml`

```yaml
sentinelcam_bastion_hostname: "chandler-gate"
```

**Purpose:** Validates that deployment runs on correct host.

**Example check:**
```yaml
assert:
  that:
    - inventory_hostname == sentinelcam_bastion_hostname
```

---

### DNS Configuration

**Where used:** `roles/bastion/templates/sentinelcam.conf.j2`

**Dynamic from inventory:**
```yaml
bastion_dnsmasq:
  generate_from_inventory: true
  inventory_groups:
    - sentinelcam_nodes
```

**Static additions:**
```yaml
bastion_dnsmasq:
  extra_static_hosts:
    - { name: "gateway", ip: "192.168.10.254" }
    - { name: "printer", ip: "192.168.10.100" }
```

---

### Health Checks

**Status:** Currently defined in `inventory/group_vars/all/site.yaml` but not actively used.

```yaml
sentinelcam_health_check_hosts:
  internal_network: "data1"  # Reserved for future ping test
  dns_test: "data1"          # Reserved for future DNS resolution test
```

**Note:** These variables are defined for future health monitoring features but are not currently implemented in any playbooks or roles.

---

## Complete Example: Adding a Second Site

### 1. Create Inventory File: `inventory/remote.yaml`

```yaml
---
all:
  children:
    sentinelcam_nodes:
      children:
        modern_nodes:
          hosts:
            north:
              ansible_host: 192.168.20.21
              node_role: outpost
            datasink2:
              ansible_host: 192.168.20.50
              node_role: datasink
    
    infrastructure:
      hosts:
        remote-gate:
          ansible_host: 192.168.20.254
          node_role: bastion
    
    outposts:
      hosts:
        north:
    
    datasinks:
      hosts:
        datasink2:

  vars:
    # Network configuration
    gateway: 192.168.20.254
    
    # Site-specific variables
    sentinelcam_site_name: "remote"
    sentinelcam_bastion_hostname: "remote-gate"
    
    sentinelcam_health_check_hosts:
      internal_network: "datasink2"
      dns_test: "datasink2"
    
    sentinelcam_vpn_gateway: "10.0.0.1"
```

### 2. Deploy to New Site

```bash
ansible-playbook -i inventory/remote.yaml playbooks/deploy-bastion.yaml
```

### 3. What Happens Automatically

1. ✅ Hostname check validates `remote-gate` (not `chandler-gate`)
2. ✅ DNS entries generated for `north` and `datasink2` from inventory
3. ✅ Health checks ping `datasink2` (192.168.20.50)
4. ✅ DNS test uses `datasink2` hostname

---

## Variable Precedence

Ansible variable precedence (highest to lowest):

1. **Extra vars** (`-e` on command line)
2. **Inventory vars** (in `hosts:` or `vars:` section)
3. **Inventory group_vars** (`inventory/group_vars/<group>/`)
4. **Inventory host_vars** (`inventory/host_vars/<host>/`)
5. **Role defaults** (`roles/<role>/defaults/main.yaml`)

### Best Practice for Multi-Site

Place site-specific variables in **inventory vars section**:

```yaml
# inventory/site2.yaml
all:
  vars:
    sentinelcam_site_name: "site2"
    sentinelcam_bastion_hostname: "site2-gate"
    # ... other site vars
```

This keeps everything for a site in one file.

---

## Common Patterns

### Pattern 1: Reference First Host of a Group

```yaml
target: "{{ groups['datasinks'][0] | default('fallback.local') }}"
```

### Pattern 2: Get IP from Hostname

```yaml
ip: "{{ hostvars[hostname]['ansible_host'] }}"
```

### Pattern 3: Check if Host is in Group

```yaml
when: "'infrastructure' in group_names"
```

### Pattern 4: Loop Through Group

```jinja
{% for host in groups['outposts'] %}
{{ host }}: {{ hostvars[host]['ansible_host'] }}
{% endfor %}
```

---

## Migration Checklist

When migrating to a new site:

### Phase 1: Inventory Setup
- [ ] Create new inventory file
- [ ] Define all hosts with `ansible_host` IPs
- [ ] Organize into groups: `sentinelcam_nodes`, `infrastructure`, etc.
- [ ] Set `sentinelcam_bastion_hostname` in vars

### Phase 2: Site Variables
- [ ] Set `sentinelcam_site_name`
- [ ] Set `sentinelcam_site_location`
- [ ] Set `sentinelcam_vpn_gateway` if using VPN
- [ ] (Optional) Configure `sentinelcam_health_check_hosts` for future use

### Phase 3: Network Configuration
- [ ] Update `gateway` IP
- [ ] Update `dns_servers` list
- [ ] Update subnet/netmask/broadcast if different

### Phase 4: Testing
- [ ] Run with `--check` flag
- [ ] Verify generated DNS configuration
- [ ] Test connectivity between nodes
- [ ] Validate health checks

---

## Troubleshooting

### Variable Not Defined

**Error:** `sentinelcam_bastion_hostname is not defined`

**Solution:** Add to inventory vars section or `inventory/group_vars/all/site.yaml`

### Wrong Host

**Error:** `This playbook should only run on chandler-gate`

**Solution:** Ensure `sentinelcam_bastion_hostname` matches the host in `infrastructure` group

### DNS Entry Missing

**Problem:** Host not appearing in DNS

**Check:**
1. Is host in `sentinelcam_nodes` group?
2. Does host have `ansible_host` defined?
3. Is `bastion_dnsmasq.generate_from_inventory: true`?

### VPN Connection Issues

**Problem:** WireGuard tunnel not establishing

**Check:**
1. Is `vault_wireguard_private_key` defined and encrypted?
2. Is `vault_wireguard_peer_public_key` correct?
3. Is `bastion_wireguard.startup_delay` set appropriately?
4. Check firewall rules allow WireGuard traffic

---

## Advanced: Multi-Environment Setup

### Structure for Multiple Sites

```
devops/ansible/
├── inventories/
│   ├── chandler/
│   │   ├── hosts.yaml
│   │   └── group_vars/
│   │       └── all/
│   │           └── site.yaml
│   ├── remote/
│   │   ├── hosts.yaml
│   │   └── group_vars/
│   │       └── all/
│   │           └── site.yaml
│   └── staging/
│       ├── hosts.yaml
│       └── group_vars/
│           └── all/
│               └── site.yaml
└── playbooks/
    └── deploy-bastion.yaml
```

### Deploy Commands

```bash
# Production site 1
ansible-playbook -i inventories/chandler/hosts.yaml playbooks/deploy-bastion.yaml

# Production site 2
ansible-playbook -i inventories/remote/hosts.yaml playbooks/deploy-bastion.yaml

# Staging environment
ansible-playbook -i inventories/staging/hosts.yaml playbooks/deploy-bastion.yaml
```

---

## Summary

**Key Concept:** Site-specific values are now **variables**, not hardcoded.

**Core Variables:**
- `sentinelcam_site_name` - Site identifier
- `sentinelcam_bastion_hostname` - Bastion hostname validation
- `sentinelcam_health_check_hosts` - Reference hosts for testing

**Dynamic Generation:**
- DNS entries from inventory groups
- Health check targets from inventory
- Validation using group membership

**Result:** Same playbooks and roles work for any site, just change the inventory.
