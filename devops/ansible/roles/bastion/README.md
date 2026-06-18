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
| `bastion_timesync` | Tunnel-independent NTP sync to the VPS (see below) |

DNS host entries are auto-generated from inventory — no manual node list maintenance.

## Time Synchronization and the WireGuard Clock Deadlock

**Symptom:** after a power failure the WireGuard tunnel never comes back up. `wg show` shows
bytes *sent* but *0 received* and no handshake; a manual `nmcli connection down/up wg0` does not
help. The VPS peer config is correct and the VPS answers other peers fine.

**Cause:** the bastion has **no battery-backed RTC** (`timedatectl` → `RTC time: n/a`). A power loss
resets the clock ~1.5 years into the past. WireGuard handshakes carry a TAI64N timestamp, and the
VPS keeps the greatest timestamp seen per peer as replay protection — so it **silently drops** the
bastion's now-stale-timestamped handshakes. NTP can't self-correct because the bastion's default
route (`allowed-ips 0.0.0.0/0`) sends NTP *into the dead tunnel*: a chicken-and-egg deadlock.

**Fix (this role):**
1. A `priority 777` routing-rule on the eth0 connection (`interfaces.yaml`) routes **all**
   VPS-bound traffic out eth0, bypassing the tunnel — so both the WireGuard handshake *and* NTP
   reach the VPS directly.
2. `chrony` is pointed at the VPS as a `prefer iburst` server with `makestep <thr> -1` so it always
   steps the clock, even a huge offset (`timesync.yaml`).
3. `wireguard-delayed-start.service` waits for time sync (`chronyc waitsync`) before bringing the
   tunnel up.
4. The WireGuard watchdog detects the sent>0 / received==0 / unsynced-clock signature and corrects
   the time before restarting — automating recovery if a boot ever races ahead of sync.

**External dependency — the VPS must serve NTP to the bastion.** This is *not* managed by this repo
(the VPS is outside the Ansible inventory). On the VPS (`blog.swanriver.dev` / cloud2):

```bash
# /etc/chrony.conf — allow the bastion's WAN network. The bastion's post-NAT
# public IP is DYNAMIC and observed to move across the ISP's range (seen at
# both 173.47.238.203 and 173.47.130.192 after storms), so allow the ISP /16.
# A /32 or /24 silently re-breaks NTP on the next lease change and re-arms the
# deadlock — WireGuard survives an IP change (key-based, roaming endpoint) but
# this IP-based ACL does not. Tighten only if the WAN IP is known static.
allow 173.47.0.0/16
sudo systemctl restart chronyd

# Open UDP 123 ONLY from that range — do NOT use --add-service=ntp (that opens
# 123 to the whole internet). chrony's own 'allow' is the real ACL; scope the
# firewall to match. chrony serves plain time only (no monlist/amplification),
# so an ISP-/16 NTP allow is low-risk versus repeated outages.
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="173.47.0.0/16" port port="123" protocol="udp" accept'
sudo firewall-cmd --reload
```

> **Inherent fragility — and two escapes.** This IP-allowlist breaks whenever the
> ISP hands the bastion a WAN IP outside the allowed range. Two ways to remove
> the fragility entirely: (1) the **hardware RTC** below (makes NTP-at-boot
> irrelevant — preferred); or (2) bootstrap time from cloud2's already-public
> **HTTPS `Date` header** over the bypass route (no allowlist at all, ~1s
> precision — enough to beat WireGuard's replay timestamp).

> **Why not `--add-service=ntp`?** It exposes UDP 123 globally. The classic NTP
> amplification risk comes from `ntpd`'s `monlist`/mode-6 control queries, which
> `chrony` does not implement (its command port is localhost-only), so chrony is
> low-risk even when open — but there is no reason to be a public time server.
> The scoped rich rule keeps the surface at exactly one source.

Confirm with `sudo chronyc clients` on the VPS (the bastion should appear) and
`chronyc sources` on the bastion (the VPS line should move from `^?` to `^*`/`^+`).

**Long-term hardware fix:** add a battery-backed RTC module (or replace the CMOS battery if x86)
so the clock survives power loss. That dissolves the deadlock entirely; the software fix above is
the resilient fallback when the clock is nonetheless wrong.

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
| `timesync` / `ntp` | chrony VPS sync + clock-deadlock fix | No |
| `watchdog` | WireGuard tunnel health watchdog + clock self-heal | No |
| `secrets` | All tasks using vault variables | **Yes** |
| `validate` / `health` | Connectivity and service health checks | No |
| `backup` | Configuration backup | No |
| `optimization` | sysctl network tuning | No |

## Files Managed (on target)

- NetworkManager connections in `/etc/NetworkManager/system-connections/`
- `/etc/dnsmasq.conf`, `/etc/dnsmasq.d/sentinelcam.conf`
- Firewall zone overrides in `/etc/firewalld/zones/`
- `/etc/systemd/system/wireguard-delayed-start.{service,timer}`
- `/etc/systemd/system/wireguard-watchdog.{service,timer}`, `/usr/local/bin/wireguard-watchdog.sh`
- Managed block in `/etc/chrony.conf` (VPS NTP server + `makestep`)
- `ipv4.routing-rules` on the eth0 NetworkManager connection (VPS bypass)

## See Also

- [Infrastructure role](../infrastructure/README.md) — DNS-only subset (legacy)
- [Ansible README](../../README.md) — vault setup and deployment patterns
