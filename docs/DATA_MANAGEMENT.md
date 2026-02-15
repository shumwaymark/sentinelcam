# Data Management and Retention

## An image capture system

SentinelCam is not a video surveillance system. It is an image frame capture system built to
feed a machine learning pipeline. That distinction shapes everything about how data is collected,
what gets kept, and what gets thrown away.

Each Outpost captures individual JPEG frames at up to 30 FPS. These are stored as discrete files,
organized by date and event, on the data sink. The computer vision and neural network analysis
that follows — face detection, recognition, object classification — operates on two-dimensional
images, not video streams. Capturing full-resolution frames preserves ground-truth detail that
compressed video would discard. This is the raw material for training and improving recognition
models over time.

The tradeoff is storage. Individual frames consume far more disk space than an equivalent
compressed video. A single busy outdoor camera can easily generate tens of thousands of images
per day. Multiply that across several Outpost nodes, and the data accumulates fast.

## Embedded constraints

These are small devices. Raspberry Pi hardware running on low voltage, with USB3-attached SSDs
providing local storage at each data sink. There is no SAN, no NAS, no cloud storage backing
any of this. The architecture assumes simple, inexpensive, permanently mounted storage — and
designs around that limitation rather than trying to overcome it with bigger hardware.

Every resource matters. CPU, memory, disk I/O, storage capacity, and network bandwidth are all
tightly coupled on these embedded nodes. Overloading one tends to cascade into the others.
As more Outpost nodes are added, additional data sinks are required to absorb the load.

Keeping storage under control is not optional — it is a survival requirement.

## What to keep, and why

Most of the data collected on any given day is routine and forgettable. The occupants walking
past a camera on the way to the car. The same delivery truck. The neighbor's cat. These events
are captured, analyzed in real time, and contribute to the ongoing understanding of what is
normal. But there is little reason to keep them around.

What *is* valuable:

- **Images with quality face detections** — training data for recognition models. A clear face
  capture from a new angle or in different lighting is worth far more than a hundred blurry frames.
- **High-confidence recognitions** — confirmation that the current model is performing well,
  useful for evaluating model accuracy over time.
- **Unusual events** — a vehicle speeding through, an unfamiliar face, activity at an odd hour.
  These are worth retaining longer, possibly archiving.
- **New subjects** — unknown faces that should be enrolled and subsequently recognized. This is
  the semi-supervised learning loop at the heart of the system.

The routine events still serve a purpose — they pass through the analysis pipeline, contribute
to real-time awareness, and provide context. They just don't need to persist.

SentinelCam knows the difference. That is the key insight driving retention policy.

## The DailyCleanup task

Rather than a blunt age-based purge, SentinelCam uses a **data-aware retention system**
implemented as a sentinel task called `DailyCleanup`. It runs on a systemd timer, scanning
backwards through recent dates and evaluating each event individually based on what the system
actually learned from it.

The cleanup task is configured with **retention profiles** — policies assigned per Outpost node
that reflect the expected activity at each camera location:

```yaml
retention_profiles:
  # Cameras focused on people (entries, walkways)
  person_tracking:
    strategy: face_quality
    retention_days: 2
    confidence_threshold: 0.975
    nodes: [lab1, alpha5]

  # Cameras covering vehicle traffic (driveways, streets)
  vehicle_tracking:
    strategy: vehicle_interest
    retention_days: 1
    speed_cutoff: 40.0
    extended_days: 2
    nodes: [east]

  # Preserve everything (special-purpose cameras)
  archive_all:
    strategy: never_delete
    nodes: []

  # Fallback for any unassigned node
  default:
    strategy: minimal_retention
    retention_days: 7
```

The evaluation logic is composable. Each event may contain multiple data types — tracking
records, face detections, face recognitions, speed measurements — and the cleanup task
checks all of them. An event is deleted only when *none* of its data types have remaining
value. If a routine event happens to contain one good face capture, it survives. If a speed
camera also caught a recognizable face, both facts are considered.

This gives fine-grained control with minimal configuration. Short retention windows keep
storage lean on busy cameras, while valuable training data and noteworthy events are
automatically preserved. The `never_delete` strategy provides an escape hatch for any
camera that should retain everything. A dry-run mode (`run_deletes: False`) produces a
report without deleting anything, useful for tuning thresholds on a new deployment.

The profiles are defined in the sentinel task configuration template
(`DailyCleanup.yaml.j2`), deployed via Ansible with the rest of the sentinel configuration.
Adding a new strategy — say, for license plate recognition or pet detection — means adding
a new evaluation block to the cleanup task class and a corresponding profile section.

## Video export

Though SentinelCam is fundamentally an image-based system, there are practical reasons to
produce video. Reviewing an event as a video clip is more intuitive than scrubbing through
individual frames. Sharing a notable event with someone outside the system requires a
format they can actually play.

The Watchtower provides a video export capability that renders stored event frames into
MP4 video with optional tracking overlays — bounding boxes, class labels, timestamps —
drawn onto each frame. Sequential events from the same camera are automatically merged
into a single clip when they fall within a configurable time gap. Exported videos can be
uploaded to an external server with time-limited secure links for sharing.

This is a convenience feature built on top of the primary data, not a replacement for it.
The individual frames remain the authoritative record. Video export is a presentation layer.

## The bigger picture

The retention system exists to keep the machine learning life cycle fed without drowning
in data. Images with useful face data flow into the training pipeline. Models get retrained.
Improved models deploy back to the sentinels and outposts. Recognition improves. The cleanup
task adapts, because better recognition changes what counts as "valuable" — events that were
once kept for their ambiguity can now be confidently discarded.

This is designed to run unattended. Set up the retention profiles for each camera, tune the
thresholds, and let it manage itself. The system should require only the bare minimum of care
and feeding.
