# CamWatcher Role

Deploys and manages **camwatcher** on datasink nodes.

## Purpose

CamWatcher subscribes to outpost image and log publishers over ZeroMQ, coordinates event-driven image
capture, and manages the interface between outpost activity and downstream processing. It stores captured
images and CSV event data, forwards detection events to sentinel for AI processing, and relays sentinel
results back to watchtower displays.

## Dependencies

- **sentinelcam_base** — user, venv, directory setup
- **imagehub** — runs alongside on datasink (shared deployment directory)

## Configuration

CamWatcher configuration is **auto-generated** from the `sentinelcam_outposts` registry in
`group_vars/all/site.yaml`. The template subscribes to all outposts where `datasink` matches the
target node's `inventory_hostname`. No manual outpost list maintenance required.

See [Outpost Registry Pattern](../../../docs/configuration/OUTPOST_REGISTRY_PATTERN.md) for details.

| Variable | Source | Purpose |
|----------|--------|---------|
| `sentinelcam_outposts` | `group_vars/all/site.yaml` | Drives auto-generated subscriptions |
| `camwatcher_service` | `group_vars/datasinks.yaml` | Service name |
| `camwatcher_install_path` | `group_vars/datasinks.yaml` | Install directory |
| `debug_mode` | host_vars (optional) | Enable debug logging |

## Deployment

```bash
# Full setup
ansible-playbook playbooks/deploy-camwatcher.yaml

# Code update
ansible-playbook playbooks/deploy-camwatcher.yaml --tags deploy

# Config change (after editing site.yaml outpost registry)
ansible-playbook playbooks/deploy-camwatcher.yaml --tags config
```

## Tags

| Tag | Scope |
|-----|-------|
| `deploy` | Application code sync |
| `config` | Generate and deploy camwatcher.yaml + systemd unit |
| `service` | Systemd service management |
| `status` | Check service state |

## File Structure (on target)

```
/home/<sentinelcam_user>/
├── camwatcher.yaml            # Application configuration
└── camwatcher/
    ├── camwatcher/            # Python package (camwatcher.py, sentinelcam/)
    └── datapump/              # Sibling package (shared directory)
```

## See Also

- [Outpost Registry Pattern](../../../docs/configuration/OUTPOST_REGISTRY_PATTERN.md)
- [DataPump role](../datapump/README.md) — sibling service, shared deployment
- [Sentinel role](../sentinel/README.md) — downstream AI processing
