# Adding a New Node

Bootstrapping a new Raspberry Pi from a bare SD card to a running SentinelCam node. The process
touches three machines (the datasink where images are burned, the bastion where DNS lives, and
the new node itself) and uses two different Ansible inventories (bootstrap, then production).
Multiple playbooks, in sequence — each one depends on the previous completing successfully.

The whole procedure takes about 40 minutes per node. Most of that is waiting for package updates.

## Before You Start

Pick a hostname and static IP from the network addressing plan. The IP ranges by role:

- **Outposts** (camera nodes): 192.168.10.20–39
- **Datasinks** (storage): 192.168.10.50–59
- **Sentinels** (AI processing): 192.168.10.60–69
- **Watchtowers** (display kiosks): 192.168.10.70–79

Check `devops/docs/network/NETWORK_ADDRESSING_STANDARD.md` for current assignments. Make sure
the IP isn't already taken in `inventory/production.yaml`.

## Step 1: Burn and Post-Process the SD Card

On the primary datasink (or any Linux machine with a card reader), write the OS image and
configure for headless boot:

```bash
# Write image
xzcat /home/ops/sentinelcam/diskimages/raspios/2024-11-19-raspios-bookworm-arm64-lite.img.xz \
  | sudo dd of=/dev/sdX status=progress bs=4M
sync

# Post-process: enable SSH, create ops user, set hostname
sudo ./devops/scripts/utilities/postprocess_pi_sd.sh /dev/sdX north MySecurePass123

sudo eject /dev/sdX
```

The post-process script mounts both partitions and configures SSH, the `ops` user with the
given password, and the hostname — all before first boot. No monitor or keyboard needed.

## Step 2: Boot and Find the DHCP Address

Insert the SD card, connect Ethernet, power on. Wait 2–3 minutes for first boot.

The node picks up a temporary DHCP address from the bastion's dnsmasq. Find it:

```bash
# From the bastion
sudo cat /var/lib/misc/dnsmasq.leases | grep north
```

Test SSH with the password from step 1:

```bash
ssh ops@192.168.10.XXX   # the DHCP address
```

## Step 3: Edit the Bootstrap Inventory

On the ramrod (Ansible control node), edit `devops/ansible/inventory/bootstrap.yaml`. Set the
temporary DHCP address as `ansible_host` and the intended final hostname/IP/role:

```yaml
new_node:
  ansible_host: 192.168.10.XXX    # current DHCP address
  ansible_user: ops
  target_hostname: north
  target_ip: 192.168.10.23        # permanent static IP
  target_role: outpost
  interface: eth0
```

Test connectivity (use `-k` since SSH keys aren't installed yet):

```bash
cd ~/sentinelcam/devops/ansible
ansible -i inventory/bootstrap.yaml new_nodes -m ping -k
```

## Step 4: Run the Bootstrap Playbook

```bash
ansible-playbook -i inventory/bootstrap.yaml playbooks/bootstrap-new-node.yaml -k
```

This sets hostname, timezone, updates all packages, installs dependencies, and — critically —
deploys the controller's SSH key to the new node. After this completes, `-k` is no longer needed.

Takes 10–15 minutes, mostly waiting on `apt upgrade`.

## Step 5: Assign the Static IP

```bash
ansible-playbook -i inventory/bootstrap.yaml playbooks/configure-static-network.yaml
```

This deploys a systemd-networkd configuration with the static IP from `bootstrap.yaml`, masks
dhcpcd and NetworkManager to prevent conflicts, then reboots the node. The playbook waits for
it to come back on the new IP.

Verify after it completes:

```bash
ping 192.168.10.23
ssh ops@192.168.10.23
```

## Step 6: Fix DNS on the Bastion

**This is the step that's easy to forget.** After the node switches from DHCP to static,
the bastion's dnsmasq still has the old DHCP lease cached. Every `.local` name resolution
for this node will return the old address until you clear it.

```bash
# On the bastion
sudo rm -f /var/lib/misc/dnsmasq.leases
sudo systemctl restart dnsmasq
```

Verify:

```bash
dig @localhost north.local +short
# Should return 192.168.10.23
```

If you skip this, Ansible runs against the production inventory will silently connect to the
wrong IP (or fail entirely if the DHCP address was reassigned). There's a
`playbooks/complete-dns-fix.yaml` that automates the full cleanup across all nodes if things
get tangled, and `playbooks/check-dns.yaml` to diagnose.

## Step 7: Add to Production Inventory

Edit `devops/ansible/inventory/production.yaml`. Two additions needed — the host definition
under `sentinelcam_nodes`, and a reference in the appropriate functional group:

```yaml
sentinelcam_nodes:
  hosts:
    north:
      ansible_host: 192.168.10.23
      node_name: north
      node_role: outpost
      interface: eth0

# And add to functional group:
outposts:
  hosts:
    north:
```

If the node needs host-specific configuration (camera settings, accelerator type, detection
parameters), create `inventory/host_vars/north.yaml`. Look at existing host_vars files for
the pattern.

Test:

```bash
ansible -i inventory/production.yaml north -m ping
```

## Step 8: Deploy Repository Access Keys

The deploy playbooks sync code to nodes via rsync from the primary datasink. That rsync
connection requires an SSH key. This must be in place before any role deployment will work.

```bash
ansible-playbook playbooks/deploy-ssh-key.yaml --limit north
```

This distributes a vault-managed deployment key to the new node and adds it to the datasink's
authorized_keys, then verifies the rsync connection works.

## Step 9: Deploy the Role

Run the deploy playbook for the node's role:

```bash
# Outpost
ansible-playbook playbooks/deploy-outpost.yaml --limit north

# Datasink (three separate services)
ansible-playbook playbooks/deploy-camwatcher.yaml --limit north
ansible-playbook playbooks/deploy-datapump.yaml --limit north
ansible-playbook playbooks/deploy-imagehub.yaml --limit north

# Sentinel
ansible-playbook playbooks/deploy-sentinel.yaml --limit north

# Watchtower
ansible-playbook playbooks/deploy-watchtower.yaml --limit north
```

These run `sentinelcam_base` (user, directories, Python venv, code sync from datasink) then
the role-specific tasks (configuration, systemd service, start).

## Verify

```bash
ssh ops@192.168.10.23
sudo systemctl status imagenode    # or camwatcher, sentinel, watchtower
journalctl -u imagenode -f         # watch the logs
```

## The Sequence, Summarized

1. Burn image, post-process SD card (`postprocess_pi_sd.sh`)
2. Boot, find DHCP address from bastion
3. Edit `inventory/bootstrap.yaml` with temp DHCP IP
4. `bootstrap-new-node.yaml` — system config + SSH keys
5. `configure-static-network.yaml` — permanent IP, reboot
6. **Clear bastion DNS cache** — the gotcha
7. Add to `inventory/production.yaml` + functional group
8. `deploy-ssh-key.yaml` — repository access (must precede deploy)
9. Deploy role playbook (`deploy-outpost.yaml`, etc.)

Steps 4–5 use the bootstrap inventory. Steps 8–9 use the production inventory.
Step 6 is on the bastion itself.
