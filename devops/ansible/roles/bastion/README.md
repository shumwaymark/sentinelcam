# Bastion Host Management Role

## Overview

This role manages the SentinelCam bastion host (chandler-gate) which serves as the network gateway providing essential infrastructure services for the isolated SentinelCam network.

**Why Rocky Linux?** Unlike the camera and processing nodes which run Raspberry Pi OS on ARM-based single-board computers, the bastion host runs Rocky Linux instead. This architectural choice provides:

- **Enterprise-grade networking**: NetworkManager integration, advanced firewall zones, and policy-based routing
- **Long-term stability**: Rocky Linux tracks RHEL with predictable 10-year lifecycle for stability
- **VPN reliability**: Supports WireGuard integration with systemd and NetworkManager for complex tunnel configurations
- **Separation of concerns**: Infrastructure services isolated from camera/AI workloads, reducing attack surface

The bastion serves as the critical bridge between the isolated camera network and external connectivity.

## Quick Start

```bash
# SSH to buzz (Ansible control node)
ssh pi@buzz
cd /path/to/sentinelcam/devops/ansible

# Deploy complete bastion configuration (includes secrets)
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --ask-vault-pass

# Deploy only network changes (no vault password needed)
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags network

# Deploy only WireGuard changes (vault password required)
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags wireguard --ask-vault-pass

# Deploy everything except WireGuard/secrets (no vault password needed)
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --skip-tags secrets
```

## Features

This role provides complete bastion host configuration including:
- WireGuard VPN tunnel management
- NetworkManager interface configuration  
- DNS/DHCP services via dnsmasq
- Firewall configuration with proper zones
- MTU/MSS optimization for VPN traffic
- Delayed startup coordination

## Architecture

### Network Interfaces
- **eth0**: External ISP connection (192.168.0.254/24) - home zone
- **enp1s0u1u4**: Internal SentinelCam network (192.168.10.254/24) - internal zone  
- **wg0**: WireGuard VPN tunnel (10.0.0.3/24) - trusted zone

### Services Managed
- **NetworkManager**: Primary network management with custom connections
- **dnsmasq**: DNS/DHCP for internal network (standalone service)
- **firewalld**: Zone-based firewall with MSS clamping
- **WireGuard**: VPN tunnel via NetworkManager (rather than using wg-quick)

### Key Features
- **DNS Conflict Resolution**: Disables NetworkManager dnsmasq to prevent conflicts
- **MTU Optimization**: MSS clamping rules for WireGuard tunnel
- **Delayed WireGuard Start**: Configurable delay to handle startup timing issues
- **Routing Rules**: Policy-based routing for VPN and local traffic

## Current Issues Addressed

### 1. DNS Service Conflicts
- Disables NetworkManager dnsmasq (`dns=none`)
- Uses standalone dnsmasq for better control
- Prevents "address already in use" errors

### 2. WireGuard Startup Timing
- Creates custom systemd service for delayed connection
- Handles network dependency ordering
- Configurable delay for problematic hardware

### 3. MTU/Packet Size Issues  
- MSS clamping in firewall trusted zone
- Optimized MTU settings for WireGuard
- Policy routing for VPN traffic

### 4. TLS Handshake Timeouts
- TCP MSS clamping to handle path MTU discovery issues
- Optimized keep-alive settings
- Firewall rules to ensure proper traffic flow

## Variables

### WireGuard Configuration
```yaml
bastion_wireguard:
  interface: wg0
  private_key: "{{ vault_wireguard_private_key }}"
  address_ipv4: "10.0.0.3/24"
  address_ipv6: "fc00:23:5::3/64"
  peer:
    public_key: "{{ vault_wireguard_peer_public_key }}"
    endpoint: "{{ vault_wireguard_endpoint }}"
    allowed_ips: "0.0.0.0/0,::/0"
    persistent_keepalive: 25
  startup_delay: 30  # seconds to wait before connecting
```

### Network Configuration
```yaml
bastion_interfaces:
  external:
    name: eth0
    ip: 192.168.0.254/24
    gateway: 192.168.0.1
    zone: home
    dns: [1.1.1.1, 8.8.8.8]
  internal:
    name: enp1s0u1u4
    ip: 192.168.10.254/24
    zone: internal
```

### DNS/DHCP Configuration
```yaml
bastion_dnsmasq:
  domain: local
  dhcp_range: "192.168.10.11,192.168.10.250,24h"
  upstream_dns: [1.1.1.1, 8.8.8.8]
  interfaces: [enp1s0u1u4, lo]
```

### Firewall Configuration
```yaml
bastion_firewall:
  mss_clamp_value: 1340
  zones:
    external: home
    internal: internal
    vpn: trusted
```

## Deployment Guide

### When Vault Password is Required

Ansible Vault encrypts sensitive variables (WireGuard keys, passwords). You need `--ask-vault-pass` only when deploying tasks that use these encrypted variables:

**Vault password required for:**
- WireGuard private key deployment (`--tags wireguard`)
- Rocky user sudo password setup
- Full deployment (includes all secrets)
- Any task tagged with `secrets`

**Vault password NOT required for:**
- Network configuration changes (`--tags network`)
- Firewall rule updates (`--tags firewall`)
- Service management (`--tags services`)
- Package installation
- Configuration changes without secrets (`--skip-tags secrets`)

### Available Deployment Tags

| Tag | Purpose | Vault Required? |
|-----|---------|----------------|
| `network` | NetworkManager configuration, DNS settings | No |
| `wireguard` | WireGuard VPN setup and key deployment | **Yes** |
| `firewall` | Firewall zones, rules, MSS clamping | No |
| `services` | Service state management | No |
| `secrets` | All tasks using vault_ variables | **Yes** |

### Common Deployment Scenarios

#### Initial Bastion Setup
```bash
# First time setup - needs vault password for WireGuard keys
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --ask-vault-pass
```

#### Network Configuration Changes
```bash
# Fix DNS conflicts or update network settings - no secrets needed
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags network
```

#### Firewall Rule Updates
```bash
# Add MSS clamping or zone changes - no secrets needed
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags firewall
```

#### WireGuard Key Rotation
```bash
# Update WireGuard keys - needs vault password
ansible-vault edit group_vars/infrastructure/vault.yaml  # Update keys first
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags wireguard --ask-vault-pass
```

#### Service Restart After Changes
```bash
# Restart services after configuration changes - no secrets needed
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags services
```

#### Configuration Changes Without Secrets
```bash
# Deploy all configuration but keep existing secrets
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --skip-tags secrets
```

### Validation and Testing Workflows

#### Pre-Deployment Validation
```bash
# Check what would change without applying (no secrets)
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --skip-tags secrets --check --diff

# Check mode with vault password
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --ask-vault-pass --check --diff
```

#### Production Safety Workflow
```bash
# 1. Validate changes first
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --check --diff

# 2. Deploy only what you need
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags network

# 3. Verify results
ansible chandler-gate -i inventory/production.yaml -m shell -a "systemctl is-active NetworkManager dnsmasq firewalld"
```

#### Verbose Logging for Troubleshooting
```bash
# Deploy with detailed output
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags network -vv
```

### Common Operations
```bash
# Restart WireGuard connection
ansible chandler-gate -i inventory/production.yaml -m shell -a "nmcli connection down wg0 && sleep 5 && nmcli connection up wg0"

# Check DNS service status
ansible chandler-gate -i inventory/production.yaml -m shell -a "systemctl status dnsmasq"

# Test internal network connectivity
ansible chandler-gate -i inventory/production.yaml -m shell -a "ping -c 2 192.168.10.50"
```

### Best Practices

1. **Start with Non-Secret Changes**: Test playbook logic first without involving secrets
2. **Use Check Mode**: Always validate with `--check --diff` before production changes
3. **Limit Vault Usage**: Only use vault password when actually changing secrets
4. **Tag Specific Tasks**: Deploy only what changed using `--tags` for faster iterations
5. **Validate Results**: Always verify deployment success with service checks

## Security Notes

### Ansible Vault for Secrets Management

Bastion deployment requires WireGuard keys and other sensitive data managed through Ansible Vault:

**Key Concepts:**
- Vault encrypts secrets with AES256 on the control node (buzz)
- Decryption happens only in memory during deployment
- Target hosts receive final configuration, never see vault file
- Encrypted vault files are safe to commit to git

**Required Secrets:**
```yaml
vault_wireguard_private_key: "your_private_key"
vault_wireguard_peer_public_key: "peer_public_key"
vault_wireguard_endpoint: "vpn.example.com"
vault_wireguard_port: "51820"
```

### Vault Setup Process

```bash
# 1. Create vault from template
cd devops/ansible
cp group_vars/infrastructure/vault.yaml.template group_vars/infrastructure/vault.yaml

# 2. Edit with your actual secrets
vim group_vars/infrastructure/vault.yaml

# 3. Encrypt the vault (on buzz control node)
ansible-vault encrypt group_vars/infrastructure/vault.yaml
# You'll be prompted for a vault password - choose a strong one

# 4. Deploy with vault password
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --ask-vault-pass
```

**Common Vault Operations:**
```bash
# Edit encrypted vault
ansible-vault edit group_vars/infrastructure/vault.yaml

# View vault contents without editing
ansible-vault view group_vars/infrastructure/vault.yaml

# Change vault password
ansible-vault rekey group_vars/infrastructure/vault.yaml

# Deploy without secrets (config changes only)
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --skip-tags secrets
```

**Generate WireGuard Keys:**
```bash
# On Linux/macOS
wg genkey | tee privatekey | wg pubkey > publickey

# On Windows (with WireGuard installed)
wg genkey | Out-File -Encoding ASCII privatekey
Get-Content privatekey | wg pubkey | Out-File -Encoding ASCII publickey
```

### Network Security
- Firewall zones properly configured
- Internal network isolated from external
- VPN traffic in trusted zone with MSS clamping
- NAT/masquerading enabled for internal → VPN forwarding
- ICMP forwarding permitted for diagnostics

## Monitoring & Health Checks

### Automated Checks
- WireGuard tunnel status
- DNS service availability  
- Internal network connectivity
- Internet routing via VPN

### Log Monitoring
- NetworkManager connection events
- dnsmasq DHCP lease activity
- Firewall traffic flows
- WireGuard handshake status

## Files Managed

### NetworkManager Connections
- `/etc/NetworkManager/system-connections/eth0.nmconnection`
- `/etc/NetworkManager/system-connections/enp1s0u1u4.nmconnection`
- `/etc/NetworkManager/system-connections/wg0.nmconnection`

### DNS Configuration
- `/etc/dnsmasq.conf`
- `/etc/dnsmasq.d/sentinelcam.conf`

### Firewall Configuration
- `/etc/firewalld/zones/trusted.xml` (MSS clamping)

### Custom Services
- `/etc/systemd/system/wireguard-delayed-start.service`
- `/etc/systemd/system/wireguard-delayed-start.timer`

## Integration with SentinelCam

### Dependencies
- All SentinelCam nodes depend on bastion for:
  - DNS resolution
  - Internet access via VPN
  - Internal network DHCP (for dynamic devices)

### Deployment Pipeline
- Bastion updates should be deployed before application updates
- Network changes require coordination with dependent services
- WireGuard restarts may temporarily disrupt external connectivity

## Future Enhancements

### Planned Features
- Automatic MTU discovery and optimization
- Advanced traffic shaping for video streams
- Integration with monitoring systems
- Backup VPN endpoint configuration

### Monitoring Integration
- Health checks for all managed services
- Performance metrics collection
- Alert integration for connectivity issues
