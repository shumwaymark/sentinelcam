# Video Export and External Sharing

## What it does

The watchtower's share button produces an on-demand video export of a captured event and
makes it available for external sharing. An Outpost generally captures event data only while
there is motion, so a single real-world event often spans multiple sequential captures with
brief pauses between them. The export pipeline merges these segments into a single MP4 video,
complete with bounding box overlays and timestamp labeling drawn from tracking data.

The finished video travels through three stages:

```
Watchtower (render) → Datasink (archive copy) → VPS (public availability) → Telegram notification
```

A copy lands on the primary datasink for internal access. A second copy goes to an external
VPS via SCP, where Nginx serves it through a time-limited secure link. That link is delivered
as a Telegram message — anyone with the link can view the video until it expires.

## The export pipeline

`VideoExporter` runs as a `multiprocessing.Process` — a daemon subprocess that handles
rendering without blocking the watchtower's main tkinter UI. The watchtower polls a progress
queue every 200ms and paints a semi-transparent overlay with phase status and a progress bar.

The pipeline has five phases:

**Detecting** — Scans the event index for sequential events from the same camera view.
Works backward and forward from the selected event, merging captures that fall within
a configurable time gap (default 30 seconds, up to 10 events). This captures the full
scope of activity that a user would consider "one event."

**Rendering** — Fetches JPEG frames from the DataPump, decodes with `simplejpeg`, draws
tracking overlays (`TextHelper` class: bounding boxes, class names, adaptive text color),
and writes frames through `cv2.VideoWriter`. Each merged segment opens with a brief
date/time header. FPS is calculated adaptively from frame timestamps.

**Optimizing** — A post-render `ffmpeg` pass re-encodes with `libx264 baseline` profile
and `+faststart` flag. The baseline profile ensures broad device compatibility. The
faststart flag moves the MP4 moov atom to the beginning of the file so the video can
begin playing before the full download completes — essential for the browser-based viewing
that a shared link implies.

**Transferring** — SCP to the datasink's `video_archives` directory. This is the internal
archive copy. (Note: cleanup of this archive is currently unmanaged — a to-do item.)

**Uploading** — If VPS upload is enabled, a second SCP sends the file to the VPS export
directory. On success, generates the secure link and sends the Telegram notification.
If VPS upload or notification fails, the export still succeeds — the datasink copy exists
regardless.

## The network path

The SentinelCam embedded network connects to the VPS through a WireGuard tunnel via the
bastion host. The tunnel runs on a private 10.0.0.0/24 segment. The watchtower reaches
the VPS over this tunnel — no direct internet exposure from any Pi node.

An SSH key dedicated to VPS uploads is stored in Ansible vault and deployed to watchtower
nodes by the watchtower role's `ssh_keys.yaml` task. The key is written to
`~/.ssh/sentinelcam_vps` and an SSH config entry maps the VPS host to that identity file.
This is the only purpose of this key — it grants SCP access to the export directory and
nothing else.

## Nginx secure links

The VPS runs Nginx with the `http_secure_link_module`. The `/sentinelcam_exports/` location
block validates every request against two URL parameters: an MD5 hash and a Unix expiry
timestamp.

The hash is computed as:

```python
md5_input = f"{expires}{uri_path}{secret}"
md5_binary = hashlib.md5(md5_input.encode()).digest()
md5_base64 = urlsafe_b64encode(md5_binary).decode().rstrip('=')
```

Note: Nginx `secure_link` uses base64url encoding (`-` and `_` instead of `+` and `/`).
Standard `b64encode` will produce intermittent 403 failures whenever the hash contains
those characters.

The Nginx directive mirrors this:

```nginx
secure_link_md5 "$secure_link_expires${uri}SECRET";
```

The three inputs — expiry timestamp, URI path, and shared secret — must match exactly
between the Python code that generates the link and the Nginx config that validates it.
A mismatch on any component produces a 403. An expired timestamp produces a 410.

The shared secret is stored in vault as `vault_vps_secure_link_secret` and appears in
the watchtower config as `vps_upload.secure_link_secret`. It is also hardcoded in the
Nginx config on the VPS. These must be the same value.

Link expiry defaults to 72 hours. A cron job on the VPS deletes MP4 files older than
72 hours from the export directory, so files may be cleaned up as their links expire.

## Telegram notification

On successful VPS upload, the exporter sends a Markdown-formatted message to a Telegram
bot with: camera view name, timestamp, event count, estimated duration, link expiry time,
and a clickable download URL. The bot token and chat ID come from vault variables deployed
through the watchtower config template.

Notification failure is non-fatal. The video is already on the VPS and the datasink — the
link still works even if the message doesn't arrive.

## Configuration

The `video_export` section in `watchtower.yaml` controls the entire pipeline. The Ansible
template (`watchtower.yaml.j2`) conditionally generates the VPS upload and Telegram blocks
only when the required vault variables are defined — without them, the share button still
produces a local export but skips external sharing.

Key settings:

| Setting | Default | Purpose |
|---------|---------|---------|
| `max_event_gap_seconds` | 30 | Maximum gap between captures to merge |
| `max_merged_events` | 10 | Upper limit on segments per export |
| `include_overlays` | true | Draw bounding boxes and labels |
| `header_duration_frames` | 60 | Frames to display date/time header |
| `local_temp_dir` | `/tmp/watchtower_exports` | Scratch space for rendering |
| `output_dir` | datasink path | SCP destination for archive copy |
| `vps_upload.enabled` | false | Enable VPS upload and sharing |
| `vps_upload.link_expiry_hours` | 24 | Secure link lifetime |

## Vault variables

| Variable | Purpose |
|----------|---------|
| `vault_vps_ssh_host` | VPS address (WireGuard tunnel IP) |
| `vault_vps_ssh_user` | SCP login user |
| `vault_vps_ssh_port` | SSH port |
| `vault_vps_ssh_private_key` | Ed25519 private key for SCP access |
| `vault_vps_secure_link_secret` | Shared secret for Nginx secure_link |
| `vault_telegram_bot_token` | Telegram Bot API token |
| `vault_telegram_chat_id` | Telegram chat/user ID for notifications |

## VPS setup

The VPS is an external server — not part of the embedded SentinelCam network and not
managed by Ansible. Its configuration is manual:

- Install Nginx with `http_secure_link_module` (standard in RHEL/Rocky packages)
- Create the export directory (`/var/www/sentinelcam_exports`)
- Add a location block for `/sentinelcam_exports/` with `secure_link` validation
- Configure SSL (Let's Encrypt or equivalent)
- Add a cron job to delete expired MP4 files (e.g., `find ... -mtime +2 -delete`)
- Deploy the watchtower's public SSH key to `authorized_keys`

The production Nginx config is maintained at `nginx.conf` in the repository root. This
is versioned as a reference copy — changes are applied to the VPS manually. Ansible does
not have SSH access to the VPS and cannot deploy to it.

## Key files

| File | Purpose |
|------|---------|
| `watchtower/watchtower/video_exporter.py` | Export pipeline (684 lines) |
| `watchtower/watchtower/watchtower.py` | UI integration, progress overlay |
| `devops/ansible/roles/watchtower/tasks/ssh_keys.yaml` | VPS key deployment |
| `devops/ansible/roles/watchtower/templates/watchtower.yaml.j2` | Config template |
| `nginx.conf` | Production Nginx config (reference copy) |
| `devops/ansible/inventory/group_vars/all/vault.yaml.template` | Vault variable reference |

## Open items

- **Datasink video archive cleanup** — Exports copied to the datasink's `video_archives`
  directory are not currently managed by any retention policy. The VPS has its cron-based
  cleanup, but the internal copies accumulate indefinitely.
