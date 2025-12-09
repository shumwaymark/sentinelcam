# Outpost Registry Pattern

## Overview

The **Outpost Registry** is a data-driven configuration pattern that centralizes all outpost node metadata in a single location (`inventory/group_vars/all/site.yaml`). This eliminates configuration duplication across multiple roles and provides a single source of truth for the relationships between outposts, cameras, views, and datasinks.

## Problem Solved

Previously, outpost configuration was scattered across multiple locations:
- **Inventory**: Host definitions with `ansible_host`, `node_name`, `node_role`
- **Camwatcher defaults**: `sentinelcam_outposts` with view names and connection URLs
- **Watchtower defaults**: `watchtower_outposts` and `watchtower_views` with duplicate mappings

This duplication caused maintenance burden and risk of inconsistency across the system.

## Architecture

### Data Flow

```
inventory/group_vars/all/site.yaml (sentinelcam_outposts)
    ↓
    ├─→ Camwatcher template → camwatcher.yaml (filtered by datasink)
    ├─→ Watchtower template → watchtower.yaml (filtered by monitored outposts)
    └─→ ImageNode template → imagenode.yaml (uses imagenode_config from inventory/host_vars)
```

### Key Concepts

1. **Outpost**: A physical node running imagenode with one or more cameras
2. **Camera**: A physical camera device (P1, P2, etc.) on an outpost
3. **Viewname**: A data tag assigned to each camera for identification (e.g., "Front", "PiCamera")
4. **Datasink**: A node running camwatcher + datapump that processes data from specific outposts
5. **Watchtower**: A node that displays live views from outposts (queries datasinks for data)

### Relationships

- **One outpost → One datasink** (many-to-one): Each outpost's data is processed by exactly one datasink
- **One outpost → Many cameras** (one-to-many): An outpost can have multiple physical cameras
- **One camera → One viewname** (one-to-one): Each camera has exactly one viewname tag for identification
- **One datasink → Many outposts** (one-to-many): A datasink can process data from multiple outposts
- **One watchtower → Many outposts** (many-to-many): Watchtowers can monitor any subset of outposts

## Configuration Structure

### Site-Level Registry (`inventory/group_vars/all/site.yaml`)

```yaml
sentinelcam_outposts:
  <outpost_hostname>:           # Must match inventory_hostname
    datasink: <datasink_hostname>  # Which datasink processes this outpost
    cameras:
      <camera_id>:              # P1, P2, etc. (matches imagenode.yaml)
        viewname: <view_name>   # View identifier (matches imagenode.yaml viewname)
        description: "Human readable description"
        resolution: [width, height]
```

**Port Assignment**: Ports are automatically assigned from `inventory/group_vars/all/sentinelcam_ports.yaml`:
- `imagenode_publisher` (default: 5567) - ZMQ image frame publisher
- `imagenode_logging` (default: 5565) - ZMQ log publisher

### Example: Single Camera Per Outpost

```yaml
sentinelcam_outposts:
  east:
    datasink: data1
    cameras:
      P1:
        viewname: Front
        description: "Front Driveway"
        resolution: [640, 360]
```

### Example: Multiple Cameras Per Outpost

```yaml
sentinelcam_outposts:
  barn:
    datasink: data2
    cameras:
      P1:
        viewname: Stalls
        description: "Horse Stalls Interior"
        resolution: [1024, 768]
      P2:
        viewname: Entrance
        description: "Barn Entrance"
        resolution: [640, 480]
```

**Note**: Ports are automatically assigned from `sentinelcam_ports.yaml` (imagenode_publisher: 5567, imagenode_logging: 5565). If an outpost needs non-standard ports (e.g., multiple cameras), configure them in the outpost's `imagenode.yaml` and override in the registry if needed.

## Template Usage

### Camwatcher Template (`roles/camwatcher/templates/camwatcher.yaml.j2`)

The camwatcher template filters the registry to **only include outposts assigned to this datasink**:

```jinja
outpost_nodes:
{% for outpost_name, outpost_config in sentinelcam_outposts.items() %}
{%   if outpost_config.datasink == inventory_hostname %}
{%     set outpost_host = hostvars[outpost_name].ansible_host | default(outpost_name) %}
{%     for camera_id, camera_config in outpost_config.cameras.items() %}
{%       set logger_port = camera_config.logger_port | default(sentinelcam_ports.imagenode_logging) %}
{%       set image_port = camera_config.image_port | default(sentinelcam_ports.imagenode_publisher) %}
  {{ outpost_name }}:
    view: {{ camera_config.viewname }}
    logger: tcp://{{ outpost_host }}:{{ logger_port }}
    images: tcp://{{ outpost_host }}:{{ image_port }}
{%     endfor %}
{%   endif %}
{% endfor %}
```

**Key Features:**
- Filters by `outpost_config.datasink == inventory_hostname`
- Resolves `ansible_host` from inventory via `hostvars[]`
- Generates ZMQ URLs dynamically from registry ports

### Watchtower Template (`roles/watchtower/templates/watchtower.yaml.j2`)

The watchtower template uses **all outposts** (or a filtered subset) and auto-generates:
1. **Ring buffers** from unique resolutions
2. **Datapumps** from unique datasinks
3. **Outposts** with image publishers and datasink mappings
4. **Views** with descriptions and sizes

```jinja
# Auto-generate ring buffers from unique resolutions
ring_buffers:
{% set ring_buffers_generated = {} %}
{% for outpost_name, outpost_config in sentinelcam_outposts.items() %}
{%   if outpost_name in watchtower_monitored_outposts %}
{%     for camera_id, camera_config in outpost_config.cameras.items() %}
{%       set resolution_key = '%dx%d' | format(camera_config.resolution[0], camera_config.resolution[1]) %}
{%       if resolution_key not in ring_buffers_generated %}
{%         set _ = ring_buffers_generated.update({resolution_key: camera_config.resolution}) %}
  {{ resolution_key }}: (({{ camera_config.resolution[0] }}, {{ camera_config.resolution[1] }}), 5)
{%       endif %}
{%     endfor %}
{%   endif %}
{% endfor %}
```

**Key Features:**
- Filters by `watchtower_monitored_outposts` (defaults to all outposts, overridable per host)
- Deduplicates resolutions for ring buffer allocation
- Auto-discovers unique datasinks for datapump connections
- Resolves both `ansible_host` (for outposts) and datasink hostnames from inventory

## Watchtower Filtering

By default, watchtowers monitor **all outposts** in the site:

```yaml
# In group_vars/all/site.yaml
watchtower_monitored_outposts: "{{ sentinelcam_outposts.keys() | list }}"
```

### Limit Specific Watchtower to Subset

Create `host_vars/wall1.yaml`:

```yaml
watchtower_monitored_outposts:
  - east
  - lab1
  # wall1 will only show views from east and lab1
```

This generates a watchtower.yaml with only the specified outposts' views, reducing resource usage and simplifying the UI.

## Adding a New Outpost

### Step 1: Add to Inventory

Edit `inventory/production.yaml`:

```yaml
modern_nodes:
  hosts:
    barn:
      ansible_host: 192.168.10.23
      node_name: barn
      node_role: outpost
      interface: eth0

outposts:
  hosts:
    barn:
```

### Step 2: Add to Site Registry

Edit `group_vars/all/site.yaml`:

```yaml
sentinelcam_outposts:
  barn:
    datasink: data1          # or data2, depending on load balancing
    cameras:
      P1:
        viewname: BarnCam
        description: "Barn Interior"
        resolution: [640, 480]
```

### Step 3: Deploy Configuration

```bash
# Deploy imagenode to the new outpost
ansible-playbook playbooks/deploy-outpost.yaml --limit barn

# Update datasink (data1) camwatcher configuration
ansible-playbook playbooks/deploy-datasink.yaml --limit data1

# Update all watchtowers to include the new outpost
ansible-playbook playbooks/deploy-watchtower.yaml
```

**That's it!** No template changes, no role defaults to update. The registry propagates automatically.

## Adding a Second Camera to an Outpost

Edit `group_vars/all/site.yaml`:

```yaml
sentinelcam_outposts:
  east:
    datasink: data1
    cameras:
      P1:
        viewname: Front
        description: "Front Driveway"
        resolution: [640, 360]
      P2:                     # New camera
        viewname: Side
        description: "Side Yard"
        resolution: [640, 480]
```

**Note**: If P2 requires different ports (non-standard), configure them in the outpost's `imagenode.yaml`. The standard ports from `sentinelcam_ports.yaml` are used by default.

Then update the outpost's `imagenode.yaml` to define the P2 camera configuration, and redeploy datasink + watchtowers.

## Load Balancing Across Datasinks

Distribute outposts across multiple datasinks by changing the `datasink` field:

```yaml
sentinelcam_outposts:
  east:
    datasink: data1
  west:
    datasink: data2
  north:
    datasink: data1
  south:
    datasink: data2
```

Each datasink's camwatcher will automatically subscribe only to its assigned outposts. Watchtowers will query the correct datasink for each outpost's data.

## Migration from Old Configuration

### Before (Scattered Configuration)

**roles/camwatcher/defaults/main.yaml:**
```yaml
sentinelcam_outposts:
  east:
    view: "Front"
    logger_url: "tcp://east:5565"
    images_url: "tcp://east:5567"
```

**roles/watchtower/defaults/main.yaml:**
```yaml
watchtower_outposts:
  east:
    image_publisher: "tcp://east:5567"
    datapump: "data1"

watchtower_views:
  Front:
    outpost: "east"
    description: "Front Driveway"
    size: [640, 360]
```

### After (Centralized Registry)

**group_vars/all/site.yaml:**
```yaml
sentinelcam_outposts:
  east:
    datasink: data1
    cameras:
      P1:
        viewname: Front
        description: "Front Driveway"
        resolution: [640, 360]
```

**Ports auto-assigned from group_vars/all/sentinelcam_ports.yaml**

**roles/camwatcher/defaults/main.yaml:**
```yaml
# NOTE: Outpost configuration moved to group_vars/all/site.yaml
```

**roles/watchtower/defaults/main.yaml:**
```yaml
# NOTE: Outpost/view configuration moved to group_vars/all/site.yaml
```

## Validation

### Check Generated Camwatcher Config

```bash
ansible data1 -m template \
  -a "src=roles/camwatcher/templates/camwatcher.yaml.j2 dest=/tmp/camwatcher-preview.yaml"

ansible data1 -m fetch \
  -a "src=/tmp/camwatcher-preview.yaml dest=./preview/ flat=yes"

cat preview/camwatcher-preview.yaml
```

### Check Generated Watchtower Config

```bash
ansible wall1 -m template \
  -a "src=roles/watchtower/templates/watchtower.yaml.j2 dest=/tmp/watchtower-preview.yaml"

ansible wall1 -m fetch \
  -a "src=/tmp/watchtower-preview.yaml dest=./preview/ flat=yes"

cat preview/watchtower-preview.yaml
```

### Verify Outpost-to-Datasink Mapping

```bash
ansible-playbook -i inventory/production.yaml playbooks/validate-configuration.yaml \
  --tags outpost_registry
```

## Troubleshooting

### Issue: Camwatcher not subscribing to an outpost

**Check:**
1. Does the outpost exist in `sentinelcam_outposts`?
2. Does `outpost_config.datasink` match the camwatcher's `inventory_hostname`?
3. Does the outpost exist in inventory with correct `ansible_host`?

**Debug:**
```bash
ansible-playbook playbooks/deploy-datasink.yaml --limit data1 --check --diff -vv
```

### Issue: Watchtower not showing a view

**Check:**
1. Does the outpost exist in `sentinelcam_outposts`?
2. Is the outpost in `watchtower_monitored_outposts` for this watchtower?
3. Does the view name exist in the outpost's camera configuration?

**Debug:**
```bash
ansible wall1 -m debug -a "var=watchtower_monitored_outposts"
ansible wall1 -m debug -a "var=sentinelcam_outposts"
```

### Issue: Port conflicts with multiple cameras

By default, all cameras use the standard ports from `sentinelcam_ports.yaml`. If an outpost has multiple physical cameras, you must configure non-standard ports in the outpost's `imagenode.yaml`. The registry uses the standard ports for connection URLs.

For advanced configurations requiring per-camera port overrides, you can optionally add `image_port` and `logger_port` attributes to override the defaults:

```yaml
sentinelcam_outposts:
  multi_cam:
    datasink: data1
    cameras:
      P1:
        viewname: View1
        resolution: [640, 480]
        # Uses default ports from sentinelcam_ports.yaml
      P2:
        viewname: View2
        resolution: [640, 480]
        image_port: 5568    # Optional override
        logger_port: 5566   # Optional override
```

**Best practice**: Use standard ports unless you have multiple physical cameras on one outpost.

## Best Practices

1. **Use descriptive view names**: "Front", "PiCamera", not "Camera1", "Camera2"
2. **Document resolutions**: Match exactly what imagenode.yaml defines
3. **Consistent port numbers**: Use standard 5567/5565 unless conflicts exist
4. **Balance datasink load**: Distribute outposts evenly across datasinks
5. **Test changes**: Use `--check --diff` before deploying
6. **Version control**: Commit `site.yaml` changes with descriptive messages

## Future Enhancements

Potential extensions to this pattern:

1. **Auto-discovery**: Outposts could register themselves dynamically
2. **Health monitoring**: Track which outposts are online/offline
3. **Dynamic reconfiguration**: Hot-reload camwatcher/watchtower when registry changes
4. **Multi-site federation**: Extend registry to support cross-site outpost viewing
5. **Role-based access**: Define which watchtowers can see which outposts based on security roles
