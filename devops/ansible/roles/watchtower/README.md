# Watchtower Role

Deploys and manages Watchtower live view display service on dedicated wall-mounted touchscreen consoles.

## Purpose

Watchtower provides real-time visualization of camera feeds from multiple outposts, displays AI detection events from Sentinel, and offers interactive controls for system operations. Designed for wall-mounted touchscreen displays (typically Raspberry Pi with official touchscreen).

## Supported Nodes

- **Watchtowers**: wall1 (any node in `watchtowers` group)

## Dependencies

- **sentinelcam_base**: Must run first to provision Python environment and system packages
- **Outposts**: ImageNode camera sources providing live feeds
- **Datasinks**: DataPump services providing event data and historical images
- **Sentinel**: AI processing results published via ZeroMQ

## Variables

### Core Configuration

Watchtower configuration is **auto-generated** from the `sentinelcam_outposts` registry in `group_vars/all/site.yaml`. The template automatically creates:

1. **Ring buffers** from unique image resolutions across all monitored outposts
2. **Datapump connections** from unique datasinks
3. **Outpost subscriptions** with image publishers and datasink mappings  
4. **View definitions** with descriptions and sizes

### Monitored Outposts

By default, watchtowers monitor **all outposts** in the site. To limit a specific watchtower to a subset, create `host_vars/<hostname>.yaml`:

```yaml
# Example: host_vars/wall1.yaml
watchtower_monitored_outposts:
  - east
  - alpha5
  # Only these outposts will be displayed on wall1
```

### Optional Overrides

```yaml
watchtower_service: watchtower      # Service name
watchtower_user: pi                 # Service user (dedicated watchtower nodes use pi)
watchtower_socket_dir: /home/pi/watchtower/sockets  # IPC socket directory
watchtower_default_view: PiCamera   # Default view on startup
watchtower_viewfps: true            # Display FPS counter
debug_mode: false                   # Enable debug logging
```

## Deployment

### Initial Setup

```bash
# Deploy full service (includes config, systemd, code)
ansible-playbook playbooks/deploy_watchtower.yaml
```

### Code Updates (Most Common)

```bash
# From Windows workstation
python devops/scripts/sync/deploy.py watchtower

# From buzz
ansible-playbook playbooks/deploy-watchtower.yaml --tags deploy
```

### Configuration Updates

```bash
# Edit config, then deploy
ansible-playbook playbooks/deploy-watchtower.yaml --tags config
```

## File Structure

```
/home/<sentinelcam_user>/
├── watchtower.yaml         # application configuration
└── watchtower/             
    ├── watchtower/         # Python package
    │   ├── watchtower.py   # Main application
    │   ├── images/         # graphics for UI
    │   └── sentinelcam/    # common libraries
    └── sockets/            # posix sockets for IPC

Config file: /home/<sentinelcam_user>/watchtower.yaml
Service: /etc/systemd/system/watchtower.service
```

## Configuration

### Example watchtower.yaml

```yaml
%YAML 1.0
---
# Settings file watchtower.yaml 
socket_dir: /home/pi/watchtower/sockets
sentinel: tcp://sentinel:5565  # result publisher 
default_view: PiCamera
viewfps: True

# For ring buffer allocations, any potential image size must be
# known in advance. Parameters are ((width, height), buffer_length)
ring_buffers:
  xga: ((1024, 768), 5)
  vga: ((640, 480), 5)
  sd: ((640, 360), 5)

# The list of known datapumps
datapumps: 
  data1: tcp://data1:5556

# Each outpost (node) has an image publisher and an associated datapump.
outposts:
  east:
    image_publisher: tcp://east:5567
    datapump: data1 
  outpost:
    image_publisher: tcp://lab1:5567
    datapump: data1 

# Every camera view is provided by an outpost node. Note that
# each outpost can potentially support multiple views.
outpost_views: 
  Front: 
    outpost: east
    description: Front Driveway
    size: (640, 360)
  PiCamera:
    outpost: outpost
    description: Lab Workstation Desktop 
    size: (640, 480)
```

## Display Capabilities

### Live Camera Feeds

- **Real-time streaming**: Live image feeds from all configured outposts
- **Multi-camera switching**: Tap/click to switch between camera views
- **Resolution auto-detection**: Automatically handles different camera resolutions via ring buffers
- **Frame rate display**: Optional FPS counter for performance monitoring

### AI Event Visualization

- **Detection overlays**: Bounding boxes for detected objects/faces
- **Recognition results**: Face identification labels from Sentinel
- **Event timestamps**: Time-based event correlation
- **Task results**: Display AI processing results (person detection, face recognition, etc.)

### Interactive Controls

- **View selection**: Choose which camera/outpost to display
- **Event playback**: Review historical events from DataPump
- **System status**: Check connectivity and service health
- **Touch interface**: Optimized for capacitive touchscreen input

## Integration

### With ImageNodes (Outposts)

Subscribes to imagenode publishers via ZeroMQ PUB/SUB pattern:
- Receives live JPEG frames from camera nodes
- Supports multiple simultaneous camera feeds
- Automatic reconnection on network interruptions

### With DataPumps (Datasinks)

Queries DataPump services for historical data:
- Retrieve past events and images
- Access stored detection results
- Pull event metadata (timestamps, detection types, etc.)

### With Sentinel (AI Processing)

Subscribes to Sentinel's result publisher:
- Receives real-time AI detection events
- Displays bounding boxes and recognition labels
- Shows task completion notifications

## Troubleshooting

### Service Won't Start

```bash
# Check logs
ssh wall1 'sudo journalctl -u watchtower -n 50'

# Common issues:
# - Display not found: Verify DISPLAY environment variable is set
# - Permission denied: Check X11 display permissions
# - Module not found: Verify Python packages installed (pygame, opencv, zmq)
# - Socket directory missing: Check socket_dir exists with correct permissions
```

### No Camera Feeds

```bash
# Check if watchtower can connect to outposts
ssh wall1 'nc -zv east 5567'  # Test imagenode publisher

# Verify outposts are publishing
ssh east 'sudo netstat -tlnp | grep 5567'

# Check watchtower logs for connection errors
ssh wall1 'sudo journalctl -u watchtower -f | grep "Connect"'
```

### Display Issues

```bash
# Check X11 display
ssh wall1 'echo $DISPLAY'  # Should show :0 or :1

# Test display access
ssh wall1 'xdpyinfo'

# Verify touchscreen input
ssh wall1 'xinput list'

# Check graphics acceleration
ssh wall1 'vcgencmd get_mem gpu'  # Should show adequate GPU memory
```

### Images Not Updating

```bash
# Check ZeroMQ subscriptions
ssh wall1 'sudo journalctl -u watchtower -f | grep "Received"'

# Verify ring buffer status
ssh wall1 'sudo journalctl -u watchtower | grep "ring buffer"'

# Check network connectivity
ssh wall1 'ping -c 3 east'  # Test outpost connectivity
```

## Hardware Requirements

### Recommended Platform

- **Raspberry Pi 4B** (4GB RAM minimum, 8GB recommended)
- **Official 7" Touchscreen Display** (800x480 resolution)
- **32GB microSD card** (Class 10 or better)
- **Power**: Official 5V/3A USB-C power supply

### Display Configuration

```bash
# Enable GPU memory allocation
sudo raspi-config
# Performance Options → GPU Memory → 256MB

# Disable screen blanking
sudo raspi-config
# Display Options → Screen Blanking → No
```

### Touchscreen Calibration

```bash
# Install calibration tool
sudo apt-get install xinput-calibrator

# Run calibration
DISPLAY=:0 xinput_calibrator
```

## Performance

Watchtower is optimized for Raspberry Pi touchscreen displays:
- Hardware-accelerated video rendering via OpenGL
- Efficient ZeroMQ subscriptions (no polling)
- Ring buffer architecture minimizes memory allocation
- Typical CPU usage: 15-30% on RPi4 (depends on frame rate and resolution)
- Memory footprint: ~100-200MB (varies with number of ring buffers)

## Tags

- `deploy` - Deploy code only (fast)
- `config` - Deploy configuration files
- `service` - Manage systemd service
- `status` - Check service status

## See Also

- Main ansible README: `devops/ansible/README.md`
- Watchtower documentation: `watchtower/README.rst`
- Outpost Registry Pattern: `devops/ansible/OUTPOST_REGISTRY_PATTERN.md`
- ImageNode role: `roles/imagenode/README.md` (camera sources)
- DataPump role: `roles/datapump/README.md` (historical data)
- Sentinel role: `roles/sentinel/README.md` (AI processing)
