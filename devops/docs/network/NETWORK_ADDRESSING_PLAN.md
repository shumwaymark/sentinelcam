# Static IP Address Plan for SentinelCam Network

**Network**: 192.168.10.0/24  
**Gateway**: 192.168.10.254 (chandler-gate)  
**Bastion Gateway**: 10.0.0.1 (WireGuard peer via wg0 interface)  
**Last Updated**: December 7, 2025

This document records the **actual current IP assignments** for the SentinelCam prototype network. For the addressing standard and design principles, see [`NETWORK_ADDRESSING_STANDARD.md`](NETWORK_ADDRESSING_STANDARD.md).

## Infrastructure Tier

| IP Address | Hostname | Purpose | Status |
|------------|----------|---------|--------|
| 192.168.10.1 | - | Reserved for future router/switch management | Reserved |
| 192.168.10.2-9 | - | Reserved for network infrastructure | Reserved |
| 192.168.10.10 | buzz | Ansible control / jump server | ✅ Active |

## SentinelCam Application Tier

### Camera Nodes (Outposts)

| IP Address | Hostname | User | Camera Type | Status |
|------------|----------|------|-------------|--------|
| 192.168.10.20 | lab1 | pi (legacy) | PiCamera | ✅ Active |
| 192.168.10.21 | east | ops (modern) | OAK1 | ✅ Active |
| 192.168.10.22 | alpha5 | ops (modern) | PiCamera | ✅ Active |
| 192.168.10.23-39 | - | - | - | Available |

### Data Sinks

| IP Address | Hostname | User | Services | Status |
|------------|----------|------|----------|--------|
| 192.168.10.50 | data1 | ops (modern) | camwatcher, datapump, imagehub | ✅ Active |
| 192.168.10.51-59 | - | - | - | Available |

### AI/ML Pipeline Nodes

| IP Address | Hostname | User | Role | Status |
|------------|----------|------|------|--------|
| 192.168.10.60 | sentinel | pi (legacy) | AI inference (NCS2) | ✅ Active |
| 192.168.10.61-69 | - | - | - | Available |

### Wall Consoles (Watchtowers)

| IP Address | Hostname | User | Display | Status |
|------------|----------|------|---------|--------|
| 192.168.10.70 | wall1 | pi (legacy) | 7" touchscreen | ✅ Active |
| 192.168.10.71-79 | - | - | - | Available |

### Reserved

| IP Address | Hostname | User | Display | Status |
|------------|----------|------|---------|--------|
| 192.168.10.40-49 | - | - | - | Available |
| 192.168.10.80-99 | - | - | - | Available |

## Management & Services

| IP Address | Hostname | User | Purpose | Status |
|------------|----------|------|---------|--------|
| 192.168.10.100 | deepend | pyimagesearch | ML training (Jetson Nano) | ✅ Active |
| 192.168.10.101 | librarian | ops | Data management (planned) | Planned |
| 192.168.10.102-199 | - | - | - | Available |

## DHCP Reservation Range

| IP Address | Purpose | Status |
|------------|---------|--------|
| 192.168.10.200-220 | DHCP pool (laptops, transient devices, etc.) | Reserved |

## Infrastructure & Gateway

| IP Address | Hostname | Interfaces | Purpose | Status |
|------------|----------|------------|---------|--------|
| 192.168.10.254 | chandler-gate | eth0: 192.168.0.254/24<br>enp1s0u1u4: 192.168.10.254/24<br>wg0: 10.0.0.3 | Bastion/Gateway<br>External: ISP gateway 192.168.0.1<br>Internal: SentinelCam network<br>VPN: WireGuard peer gateway 10.0.0.1 | ✅ Active |

## Notes

- **Legacy nodes**: Use `pi` user (Raspberry Pi OS Bullseye or earlier)
- **Modern nodes**: Use `ops` user (Raspberry Pi OS Bookworm+)
- **Addressing ranges**: Follow the standard defined in `NETWORK_ADDRESSING_STANDARD.md`
- **External network**: Site-specific, defined in inventory files

## Related Documentation

- [**Network Addressing Standard**](NETWORK_ADDRESSING_STANDARD.md) - Design principles and ranges
- [**Bastion Role README**](../../ansible/roles/bastion/README.md) - Gateway configuration
- [**Multi-Site Deployment**](../configuration/MULTI_SITE_DEPLOYMENT.md) - Site-specific addressing
