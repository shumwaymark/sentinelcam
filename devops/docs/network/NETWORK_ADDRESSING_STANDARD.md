# SentinelCam Network Addressing Standard

**Date**: December 7, 2025  
**Status**: Active Standard

## Overview

This document defines the network addressing standards for SentinelCam deployments. The architecture uses a dual-network design with a bastion host separating external (site-specific) and internal (standardized) networks.

## Network Architecture

```
Internet
   |
[External Network - SITE SPECIFIC]
   |
[Bastion Host - chandler-gate, steel-gate, etc.]
   |
[Internal Network - 192.168.10.0/24 - STANDARDIZED]
   |
[SentinelCam Nodes]
```

## Internal Network Standard (REQUIRED)

### **192.168.10.0/24** - SentinelCam Internal Network

This network is **standardized across all sites** and for consistency and simplified managenent, should not be changed.

#### Network Parameters
- **Network**: `192.168.10.0/24`
- **Netmask**: `255.255.255.0`
- **Broadcast**: `192.168.10.255`
- **Gateway**: `192.168.10.254` (Bastion internal interface)
- **DNS Server**: `192.168.10.254` (Bastion dnsmasq)
- **DHCP Range**: `192.168.10.200` - `192.168.10.250`

#### Reserved IP Address Ranges

| Range | Purpose | Notes |
|-------|---------|-------|
| `.1` - `.19` | Reserved for infrastructure 
| `.20` - `.39` | Outpost nodes | Camera/sensor nodes |
| `.40` - `.49` | Reserved | Future expansion |
| `.50` - `.59` | Datasink nodes | Storage/processing hubs |
| `.60` - `.69` | AI processing nodes | sentinel, etc. |
| `.70` - `.79` | Watchtower nodes | Monitoring/alerting |
| `.80` - `.99` | Reserved | Future expansion |
| `.100` - `.199` | Service nodes | Supporting services |
| `.200` - `.250` | DHCP pool | Temporary/new devices/nomadic |
| `.251` - `.253` | Reserved | Future use |
| `.254` | Gateway | Bastion internal interface |

#### Prototype Node Assignments

**Infrastructure:**
- `192.168.10.10` - buzz (ramrod node)
- `192.168.10.254` - Bastion host 

**Outposts (Camera Nodes):**
- `192.168.10.20` - lab1
- `192.168.10.21` - east
- `192.168.10.22` - alpha5
- `192.168.10.23-39` - Available for additional outposts

**Datasinks:**
- `192.168.10.50` - data1 (primary)
- `192.168.10.51-59` - Available for additional datasinks

**Sentinels and M/L pipeline:**
- `192.168.10.60` - sentinel
- `192.168.10.61-69` - Available for expansion

**Watchtowers:**
- `192.168.10.70` - wall1
- `192.168.10.71-79` - Available for additional watchtowers

**Services:**
- `192.168.10.100` - deepend
- `192.168.10.101` - librarian

### Why This Network is Standardized

1. **Isolation**: The internal network is completely isolated behind the bastion
2. **No Conflicts**: Cannot conflict with external networks at any site
3. **Configuration Portability**: Ansible roles, scripts, and configs are portable across sites
4. **Simplified DNS**: All nodes have consistent hostnames and IPs across sites
5. **Easy Multi-Site**: Identical internal addressing makes VPN interconnection simpler

## External Network Configuration (SITE-SPECIFIC)

The external network **must be configured per site** based on the local network environment.

### Configuration Method

External network settings are defined in **site inventory variables**, not in role defaults.

#### Required Site Variables

```yaml
# In inventory files (production.yaml, site2-example.yaml, etc.)
all:
  vars:
    # External network - customize per site
    bastion_external_network:
      ip: "192.168.0.254/24"        # Bastion's IP on external network
      gateway: "192.168.0.1"        # ISP router/gateway IP
      dns: ["1.1.1.1", "8.8.8.8"]   # Upstream DNS servers
      zone: "home"                  # Firewall zone name
```

### Common External Network Scenarios

#### Home Network (ISP Router)
```yaml
bastion_external_network:
  ip: "192.168.0.254/24"       # High IP, confirm exclusion from router DHCP pool
  gateway: "192.168.0.1"       # Typical home router
  dns: ["1.1.1.1", "8.8.8.8"]
  zone: "home"
```

#### Business/Static IP
```yaml
bastion_external_network:
  ip: "10.50.1.254/24"         # Business internal network
  gateway: "10.50.1.1"         # Business gateway
  dns: ["8.8.8.8", "8.8.4.4"]
  zone: "external"
```

#### Direct WAN Connection
```yaml
bastion_external_network:
  ip: "203.0.113.5/29"         # Public IP block
  gateway: "203.0.113.1"       # ISP gateway
  dns: ["1.1.1.1", "8.8.8.8"]
  zone: "dmz"
```

## Interface Naming (SITE-SPECIFIC)

Physical interface names vary by hardware and are configured per site:

```yaml
# In site inventory
bastion_interface_external: "eth0"        # or eno1, enp1s0, etc.
bastion_interface_internal: "enp1s0u1u4"  # or eth1, enp2s0, etc.
```

Common patterns:
- **eth0, eth1**: Traditional naming
- **eno1, eno2**: Onboard Ethernet
- **enp1s0**: PCI slot-based naming
- **enp1s0u1u4**: USB Ethernet adapter

## VPN/WireGuard Addressing (SITE-SPECIFIC)

The WireGuard tunnel uses a separate address space for interconnecting sites:

### VPN Network Standard
- **VPN Network**: `10.0.0.0/24`
- **VPN IPv6**: `fc00:23:5::/64`
- **VPN Gateway**: `10.0.0.1` (external VPN server/hub)

### Site-Specific VPN IP Allocation

Each bastion **must have a unique VPN IP** to enable multi-site mesh networking:

| Site | Bastion | VPN IPv4 | VPN IPv6 |
|------|---------|----------|----------|
| Central/Hub | VPN Server | `10.0.0.1` | `fc00:23:5::1` |
| Site 1 (Chandler) | chandler-gate | `10.0.0.3` | `fc00:23:5::3` |
| Site 2 (AshSt) | ash-gate | `10.0.0.2` | `fc00:23:5::2` |
| Site 3 | [future] | `10.0.0.4` | `fc00:23:5::4` |
| ... | ... | `.5-.250` | `::5-::250` |

### Configuration

VPN addresses are configured in site inventory:

```yaml
# In inventory files
bastion_vpn_ipv4: "10.0.0.4/24"      # Unique per site
bastion_vpn_ipv6: "fc00:23:5::4/64"  # Unique per site
```

### Why VPN IPs are Site-Specific

1. **Multi-Site Mesh**: Different sites need unique IPs to communicate
2. **No Conflicts**: Prevents IP collisions in VPN overlay network
3. **Scalability**: Can add sites without reconfiguring existing ones
4. **Routing**: Enables site-to-site routing and failover

## DNS Configuration

### Internal DNS (dnsmasq on Bastion)
- **Domain**: `sentinelcam.local` (or just `.local`)
- Automatically populated from Ansible inventory
- All SentinelCam nodes get DNS entries
- Upstream DNS forwarded to site-specific servers

### External DNS
- Configured per site in `bastion_external_network.dns`
- Typically public resolvers (1.1.1.1, 8.8.8.8) or ISP DNS

## Configuration Examples

### Site 1 (Chandler prototype - Production)
```yaml
# inventory/production.yaml
all:
  vars:
    # External network (home ISP)
    bastion_external_network:
      ip: "192.168.0.254/24"
      gateway: "192.168.0.1"
      dns: ["1.1.1.1", "8.8.8.8"]
      zone: "home"
    
    # VPN addressing (unique per site)
    bastion_vpn_ipv4: "10.0.0.3/24"
    bastion_vpn_ipv6: "fc00:23:5::3/64"
    
    # Internal network (standard)
    subnet: 192.168.10.0/24
    gateway: 192.168.10.254
    dns_servers: [192.168.10.254, 8.8.8.8]
    
    # Site-specific interface names
    bastion_interface_external: "eth0"
    bastion_interface_internal: "enp1s0u1u4"
```

### Site 2 (Remote Location)
```yaml
# inventory/site2-example.yaml
all:
  vars:
    # External network (different ISP/network)
    bastion_external_network:
      ip: "10.0.1.254/24"
      gateway: "10.0.1.1"
      dns: ["8.8.8.8", "8.8.4.4"]
      zone: "external"
    
    # VPN addressing (unique per site - NOTE the .4 instead of .3)
    bastion_vpn_ipv4: "10.0.0.4/24"
    bastion_vpn_ipv6: "fc00:23:5::4/64"
    
    # Internal network (same standard!)
    subnet: 192.168.10.0/24
    gateway: 192.168.10.254
    dns_servers: [192.168.10.254, 8.8.8.8]
    
    # Site-specific interface names
    bastion_interface_external: "eno1"
    bastion_interface_internal: "eth1"
```

## Rationale

### Why Standardize Internal Network?
- **Portability**: Move devices between sites without reconfiguration
- **Consistency**: Same configuration across all sites
- **Isolation**: No risk of conflict with external networks
- **Simplicity**: One set of IPs to remember and document
- **VPN-Friendly**: Easier to interconnect multiple sites

### Why Site-Specific External Network?
- **Reality**: Each location has different upstream networks
- **ISP Variation**: Home networks, business networks, static IPs all differ
- **Flexibility**: Can't control what ISP/location provides
- **Separation**: Clearly separates what's standardized from what's not

## Compliance

All Ansible playbooks and roles must:
1. Use `{{ subnet }}`, `{{ gateway }}` variables for internal network
2. Use `{{ bastion_external_network }}` dict for external network config
3. NOT hardcode IPs except in role defaults as fallbacks
4. Document any deviations in role README

## See Also

- `devops/ansible/inventory/production.yaml` - Site 1 example
- `devops/ansible/inventory/site2-example.yaml` - Site 2 example
- `devops/ansible/roles/bastion/defaults/main.yaml` - Bastion configuration
- `devops/SITE_VARIABLES_REFERENCE.md` - All site-specific variables
