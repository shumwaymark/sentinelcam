# Watchtower Role

Deploys and manages **watchtower** on dedicated wall-mounted touchscreen consoles.

## Purpose

Watchtower provides real-time visualization of camera feeds from outposts, displays AI detection events
from sentinel, and offers interactive controls for event review. Designed for Raspberry Pi with
official touchscreen display, running as a kiosk-style application.

## Dependencies

- **sentinelcam_base** — user, venv, directory setup
- **Outposts** (imagenode) — live camera feed publishers
- **DataPump** — historical event data and images
- **Sentinel** — AI processing result publisher

## Configuration

Watchtower configuration is **auto-generated** from the `sentinelcam_outposts` registry in
`group_vars/all/site.yaml`. The template automatically creates ring buffer allocations, datapump
connections, outpost subscriptions, and view definitions from the registry.

By default, watchtowers monitor **all outposts**. To limit to a subset:

```yaml
# host_vars/wall1.yaml
watchtower_monitored_outposts:
  - east
  - alpha5
```

Other overrides in host_vars:

| Variable | Default | Purpose |
|----------|---------|---------|
| `watchtower_default_view` | `Front` | Default camera view on startup |
| `watchtower_viewfps` | `true` | Display FPS counter |
| `watchtower_video_export` | (see defaults) | Video export config (VPS paths, link expiry) |
| `inactivity_timeout` | `30` | Auto-pause timeout in seconds |
| `debug_mode` | `false` | Enable debug logging |

### Desktop/Kiosk Setup

The role also manages desktop environment configuration for touchscreen consoles: display settings,
screen blanking suppression, touchscreen calibration, and auto-login. These are handled via the
`desktop`, `kiosk`, and `touchscreen` tags.

## Deployment

```bash
# Full setup (new watchtower node)
ansible-playbook playbooks/deploy-watchtower.yaml

# Code update
ansible-playbook playbooks/deploy-watchtower.yaml --tags deploy

# Config change (after editing site.yaml outpost registry)
ansible-playbook playbooks/deploy-watchtower.yaml --tags config
```

## Tags

| Tag | Scope |
|-----|-------|
| `deploy` | Application code sync |
| `config` | Generate and deploy watchtower.yaml + systemd unit |
| `service` | Systemd service management |
| `desktop` / `kiosk` | Desktop environment and auto-login |
| `touchscreen` | Touchscreen calibration |
| `ssh_keys` | SSH key deployment for VPS export |
| `status` | Check service state |

## File Structure (on target)

```
/home/<watchtower_user>/
├── watchtower.yaml            # Application configuration
└── watchtower/
    ├── watchtower/            # Python package
    │   ├── watchtower.py
    │   ├── images/            # UI graphics
    │   └── sentinelcam/       # Common libraries
    └── sockets/               # IPC sockets
```

## See Also

- [Outpost Registry Pattern](../../../docs/configuration/OUTPOST_REGISTRY_PATTERN.md)
- [ImageNode role](../imagenode/README.md) — camera feed sources
- [DataPump role](../datapump/README.md) — historical data
- [Sentinel role](../sentinel/README.md) — AI processing results
