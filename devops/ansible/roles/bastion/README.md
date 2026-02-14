# Bastion Role

Configures the SentinelCam bastion host (chandler-gate), the network gateway for the isolated camera network.

## Purpose

The bastion runs Rocky Linux and provides the infrastructure services that the SentinelCam network
depends on: WireGuard VPN tunnel for external connectivity, dnsmasq for DNS/DHCP on the internal
network, firewall zone management, and network interface configuration via NetworkManager. All
SentinelCam nodes depend on the bastion for DNS resolution and internet access.

## Dependencies

None. This is infrastructure — deploy before application roles.

## Configuration

All variables are defined in `defaults/main.yaml` with sensible defaults. Sensitive values
(WireGuard keys, endpoint) are encrypted in `group_vars/infrastructure/vault.yaml`.

| Variable block | Purpose |
|----------------|---------|
| `bastion_wireguard` | VPN tunnel config (interface, peer, keys, startup delay) |
| `bastion_interfaces` | NetworkManager connections (external, internal) |
| `bastion_dnsmasq` | DNS/DHCP (domain, DHCP range, upstream DNS, host entries from inventory) |
| `bastion_firewall` | Zone assignments and MSS clamping for VPN traffic |
| `bastion_services` | Service state management |
| `bastion_network_optimization` | sysctl settings (IP forwarding, TCP BBR) |

DNS host entries are auto-generated from inventory — no manual node list maintenance.

## Deployment

Vault password is only required when deploying tasks that touch encrypted secrets (WireGuard keys,
sudo passwords). Use `--skip-tags secrets` for config-only changes.

```bash
# Full deployment (needs vault password)
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --ask-vault-pass

# Network/DNS/firewall changes only (no vault needed)
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags network

# WireGuard only (needs vault)
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --tags wireguard --ask-vault-pass

# Everything except secrets
ansible-playbook playbooks/deploy-bastion.yaml -i inventory/production.yaml --skip-tags secrets
```

## Tags

| Tag | Scope | Vault? |
|-----|-------|--------|
| `network` | NetworkManager configuration, interfaces | No |
| `wireguard` | WireGuard VPN setup and key deployment | **Yes** |
| `dns` / `dhcp` | dnsmasq configuration | No |
| `firewall` | Firewall zones, MSS clamping | No |
| `startup` | Delayed WireGuard start service/timer | No |
| `secrets` | All tasks using vault variables | **Yes** |
| `validate` / `health` | Connectivity and service health checks | No |
| `backup` | Configuration backup | No |
| `optimization` | sysctl network tuning | No |

## Files Managed (on target)

- NetworkManager connections in `/etc/NetworkManager/system-connections/`
- `/etc/dnsmasq.conf`, `/etc/dnsmasq.d/sentinelcam.conf`
- Firewall zone overrides in `/etc/firewalld/zones/`
- `/etc/systemd/system/wireguard-delayed-start.{service,timer}`

## See Also

- [Infrastructure role](../infrastructure/README.md) — DNS-only subset (legacy)
- [Ansible README](../../README.md) — vault setup and deployment patterns
