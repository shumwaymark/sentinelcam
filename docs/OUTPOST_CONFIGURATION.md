# Outpost Configuration & Tuning

Operator reference for the per-node knobs an outpost reads from its Ansible
`host_vars`. This is the **how to set it** companion to
[TRACKING_ARCHITECTURE.md](TRACKING_ARCHITECTURE.md), which covers the **why** —
the subject lifecycle, the track state machine, and the one-lifecycle/two-sources
design. Read that first if the terms *PROVISIONAL / ACTIVE / QUIESCENT*,
*relocation*, or *quiescence* are unfamiliar; the tunables below are the dials on
that machine.

All of these live under a camera's `detector:` block in
`devops/ansible/inventory/host_vars/<hostname>.yaml` and are rendered into the
deployed `imagenode.yaml` by the imagenode role template. Edit host_vars and
redeploy — never edit the deployed config on a node directly.

```yaml
imagenode_config:
  cameras:
    P1:                              # P* PiCamera, O* OAK, W* webcam
      detector:
        detectobjects: mobilenetssd  # host NN engine (mobilenetssd | none)
        accelerator: coral           # none | ncs2 | coral
        tracker: { ... }             # §1 — opts into the unified lifecycle
        motion_params: { ... }       # §3 — host-NN nodes only (the NN scheduler)
        depthai:                     # OAK nodes only
          oak_pipeline:
            crop_profiles: { ... }   # §2 — high-res crop geometry per class
        spyglass: (768, 432)         # §5 — shared buffer size; MUST match pipeline
        ROI: (10,20),(70,80)         # §5 — motion region of interest (% of frame)
        sentinel_tasks: { ... }      # §4 — post-event analysis dispatch
```

> **Retired key.** `interesting_objects` is no longer read by any code. Its
> replacement is `interesting_classes` **inside the `tracker:` dict**, a
> `TrackerConfig` field defaulting to `{person, vehicle}` — see §1.

> **§4.10 note.** `detector.tracker` is a *dict* and lives at the detector level
> for **every** node type. A dict opts the node into the unified host-tracker
> lifecycle; omit it for a publish-only node (live scene, no events). The legacy
> `tracker: none` string flag and the `depthai.tracker` sub-block are retired —
> an OAK node sets `tracker:` as a sibling of `depthai:`, not inside it.

---

## 1. `detector.tracker` — host tracker tunables

The persistent tracker's thresholds. **Every key is optional**; an omitted key
takes the code default (the replay-validated street-cam value in
`hosttracker.TrackerConfig`). These are genuinely *per-view* — a street camera, a
front-door camera, and a hallway camera each want different confirmation and
quiescence values. Tune from each node's own observed behaviour after a soak.

| Key | Default | What it does | Choosing a value |
|-----|---------|--------------|------------------|
| `min_confidence_new` | `0.0` | **Birth floor.** A detection below this may still *associate with / sustain* an existing track, but may **not** start a new one. Kills low-confidence static-blob storms at the birth site. | The common per-node knob. Set from the node's own confidence distribution after a soak. Production: east `0.60` (OAK), alpha5 / lab1 `0.50` (host NN). `0.0` disables gating. |
| `match_iou` | `0.30` | Detection↔track association floor (IoU). | `0.30` associates even fast movers with margin (street-cam moving-object consecutive-frame IoU p5 ≈ 0.73). Lower only for very fast traversal at low frame rate. |
| `confirm_obs` | `2` | Min sightings to confirm `PROVISIONAL → ACTIVE`. | `2` keeps the entry phase while rejecting one-frame ghosts; ≤2-frame real events are only ~1.7%. Raise to debounce harder (loses fast passers). |
| `confirm_time` | `0.05` | Min capture-time span (s) the `confirm_obs` sightings must straddle. | Pairs with `confirm_obs` so a burst on a single instant can't confirm. Rarely changed. |
| `quiescence_window` | `1.2` | Seconds of no relocation before an `ACTIVE` track banks to `QUIESCENT` (closing the event). | Sits below typical transit (median 1.2–1.8 s) so a real pass never reads static, yet banks a lingerer. Raise where subjects loiter normally. |
| `quiescence_iou` | `0.95` | Window-endpoint IoU above which a track is "hasn't moved". | Near-static holds 0.95–0.99; movers collapse below over the window. |
| `relocation_iou` | `0.50` | Banked-vs-now IoU **below** which a `QUIESCENT` track has "relocated" → re-opens a (departure) event. | Below the moving p25 (~0.82) so genuine relocation trips it, above jitter. This is what keeps a parked car silent through wind/glare/autofocus yet catches it pulling away. |
| `gap_misses` | `30` | **K** — max consecutive `observe()` misses before a track ENDs. (Capture-time ≈ K / fps; 30 ≈ 1.0 s at 30 fps.) | Replay K-sweep shows a wide plateau: over-fragments below ~5, flat from ~30 out to 10 s. System is insensitive across 1–10 s. |
| `quiescent_gap_misses` | `None` | Coast-K for **banked** (`QUIESCENT`) tracks. A parked subject survives this many misses before ENDing, so a brief dropout (glare/focus blink) re-associates at its banked bbox instead of ending + re-opening a fresh event. | Set **well above** `gap_misses` for cameras with parked subjects through hard lighting. `None` = fall back to `gap_misses`. |
| `interesting_classes` | `[person, vehicle]` | Base classes admitted to the tracker at ingest; everything else is dropped. | List form in YAML. Narrow it to suppress a class entirely on a given view. |
| `centroid_match_dist` | `None` | Centroid-distance association fallback (px) for the IoU=0 tail — the fastest movers / re-detect jumps that skip too far for IoU to match. | Advanced. `None` = disabled (replay-identical). Enable only if fast-mover fragmentation is observed. |
| `history_len` | `256` | Bounded per-track `(timestamp, bbox)` history depth. | Rarely changed; affects the EventSampler's phase computation window. |

```yaml
        tracker:
          min_confidence_new: 0.60     # birth floor, tuned per node
          # match_iou: 0.30            # uncomment to override a code default
          # quiescent_gap_misses: 150  # coast a parked subject through glare blinks
```

---

## 2. `crop_profiles` — OAK high-resolution crops (OAK nodes only)

Under `detector.depthai.oak_pipeline.crop_profiles`, keyed by base class
(`person`, `vehicle`). Configures the high-res crops the OAK EventSampler emits
per traversal — the input to downstream re-ID — so the geometry is per-class
(person portrait, vehicle landscape). Each field is optional; omitted keys fall
back to the validated defaults in `oak_camera.py`.

| Field | person / vehicle default | What it does |
|-------|--------------------------|--------------|
| `width`, `height` | 256×384 / 512×192 | Output crop dimensions in pixels (portrait vs landscape per class). |
| `padding` | 0.15 / 0.10 | Fractional pad added around the detection bbox before cropping. |
| `max_area_fraction` | 0.35 / 0.25 | Skip the crop if the padded bbox exceeds this fraction of the frame (subject too close/large). |
| `min_interval_s` | 0.20 / 0.15 | Emit floor — minimum seconds between crops of the same subject (throttle). |
| `min_iou` | 0.70 / 0.50 | Below this IoU vs. the last crop, treat as a new subject view worth re-emitting. |
| `edge_margin` | 0.05 / 0.05 | Skip if the raw detection bbox sits within this fraction of any frame edge. **Mandatory guard** — edge crops are the documented device crash mode; do not set to 0. |
| `max_aspect_deviation` | 0.08 / 0.15 | Reject the crop if its final aspect ratio drifts more than this fraction from the target (keeps only well-shaped subjects). |

```yaml
        depthai:
          oak_pipeline:
            crop_profiles:
              person:  { width: 256, height: 384, edge_margin: 0.05 }
              vehicle: { width: 512, height: 192, edge_margin: 0.05 }
```

---

## 3. `motion_params` — motion detector (host-NN nodes only)

On picamera / Coral nodes motion detection is the **NN scheduler**: it decides
which frames are worth a (relatively expensive) host inference. It does not drive
events. Tune these from the live motion-calibration tool. (OAK nodes run the
device NN every frame and do not use motion.)

| Key | Default | What it does |
|-----|---------|--------------|
| `varThreshold` | 128 | MOG2 background-subtraction variance threshold (higher = less sensitive). |
| `detectShadows` | false | MOG2 shadow detection. |
| `history` | 500 | MOG2 background model history length (frames). |
| `minContourW` / `minContourH` | 50 / 50 | Minimum contour width/height (px) to count as motion — filters specks. |
| `gaussianBlur` | 5 | Pre-blur kernel size for noise reduction (odd integer). |

The `ROI` key (sibling of `motion_params`, given as `(x1,y1),(x2,y2)` in percent
of frame) bounds where motion is measured.

---

## 4. `sentinel_tasks` — post-event analysis dispatch

Maps a detected base class to the Sentinel task submitted when an event closes.
The reserved `default` key runs on every event regardless of class. (Class-keyed
tasks submit at priority 1, `default` at priority 2.)

```yaml
        sentinel_tasks:
          person: GetFaces2                # run on events containing a person
          default: VehicleSpeed            # run on every event
```

---

## 5. `spyglass` and `ROI` — buffer sizing and motion region

Two detector-level settings that predate the §4.10 redesign and survived it intact.

### `spyglass` — shared memory buffer dimensions

```yaml
spyglass: (768, 432)    # MUST match the true pipeline image size
```

The `SpyGlass` runs in a separate process, so a shared memory buffer is allocated to
pass full-size frames to it. **This setting sizes that buffer.** Get it wrong and
the operation fails.

It must match the image size actually flowing through the imagenode pipeline —
normally the camera's `resolution`, but not always. In particular, `resize_width`
changes the pipeline image size and should be avoided on an outpost regardless,
being computationally expensive. Any other detectors configured on the node affect
performance too; know what else is running.

> **Why the setting exists at all.** A `Detector` initializes *before* camera startup
> completes, so the `Camera` instance has not yet learned its true image size. The only
> alternative would be deferring `SpyGlass` initialization until the first frame arrives.
> Do not guess this value — determine the true size passing through the pipeline and
> state it.

**Under Ansible you do not set this by hand.** The imagenode template renders it from
`camera.resolution`, so the two cannot drift apart. The warning above matters when
something else changes the pipeline image size after capture — which is the real reason
`resize_width` is to be avoided on an outpost.

### `ROI` — motion region of interest

```yaml
ROI: (10,20),(70,80)    # region of interest for motion detection
```

Restricts **motion detection** to a rectangular sub-region. Note the scope: a
`spyglass` and the object detection it performs always apply to the full-size image;
`ROI` bounds motion only, so it is meaningful on host-NN nodes where motion schedules
inference, and inert where `motion_detector: none`.

Corners are given as OpenCV-style `(X1,Y1),(X2,Y2)` — top-left then bottom-right — but
in **integer percentages (0–100) of frame size**, not pixels. That convention lets the
region survive a resolution change unaltered, which is exactly what a node needs when
its scene size is retuned. The default is `(0,0),(100,100)`, the full frame.

The baseline imagenode also offers `draw_roi`, `draw_time`, `draw_time_org` and
`draw_time_fontScale` for visualizing these while tuning; see *"Camera Detectors, ROI
and Event Tuning"* in the [upstream imagenode settings
documentation](https://github.com/shumwaymark/imagenode/blob/master/docs/settings-yaml.rst).

> There is **no validation** on any detector setting. A misconfiguration surfaces as an
> operational failure, and most settings have no usable default.

---

## 6. Node-level publishing and connection settings

Everything above is per-camera, under `detector:`. These four are different: they
sit at the **imagenode level**, are applied once per node regardless of how many
cameras it runs, and are what connect an outpost to a **camwatcher** at all.

```yaml
imagenode_config:
  node_name: east
  publish_cam: 5567       # port for scene image publishing (ImageZMQ PUB)
  publish_log: 5565       # port for log publishing (ZMQ PUB)
  logconfig:              # logging configuration dictionary
    interface_or_socket: tcp://*:5565
    root_topic: east      # MUST match node_name
    level: INFO           # INFO is the minimum for outpost functionality
  camwatcher: tcp://data1:5566   # optional — dynamic registration
```

### `publish_cam`

Port number for scene image publishing. Activates an `imagezmq.ImageSender`; every
frame through the camera's pipeline is published as a JPEG, so any client can
subscribe for a live feed. Achievable frame rate falls with pipeline length —
multiple cameras, larger frames, and additional detectors all compound.

### `publish_log`

Port number for log publishing over ZeroMQ. Must match the port in the
`logconfig` connection string below.

### `logconfig`

Required dictionary configuring the ZMQ PUB log handler. Once active, **all**
logger calls go through it.

| Key | Requirement |
|-----|-------------|
| `interface_or_socket` | Bind string; its port must match `publish_log`. |
| `root_topic` | Must match the node name at the top of the YAML — it is the topic subscribers filter on, and the `ote` records omit the node name precisely because it arrives this way. |
| `level` | `INFO` is required for basic outpost functionality. `DEBUG` when diagnosing. |

### `camwatcher`

**Optional.** A connection string to a running camwatcher's control port. At
startup the node sends a `CamUp` introduction, assembled from `publish_log`,
`publish_cam`, and the hostname read from the running network configuration.

This is a *dynamic, temporary* registration — a camwatcher restart clears it.
Production nodes belong in the camwatcher's own configuration (generated from the
`site.yaml` outpost registry); use this for ad hoc introductions during bring-up
and testing.

### Multiple cameras on one node

Only the **first** camera entry's port numbers are used; values on subsequent
entries are ignored. Keep them identical anyway so the configuration does not
mislead. Frames from multiple cameras **interleave** on the single stream, so
subscribers filter by `viewname` — the camwatcher always does.

> Wire formats for all of the above — the `ote` record schemas, the image stream
> text descriptors, the `CamUp` message — are specified in
> [EVENT_PROTOCOL.md](EVENT_PROTOCOL.md).

---

## See also

- [EVENT_PROTOCOL.md](EVENT_PROTOCOL.md) — the wire contract these settings
  produce: `ote` record schemas, stream descriptors, and the `CamUp` handshake.
- [TRACKING_ARCHITECTURE.md](TRACKING_ARCHITECTURE.md) — the lifecycle, state
  machine, and two-node-types design these knobs tune.
- [OUTPOST_HISTORY.md](OUTPOST_HISTORY.md) — retired settings and what replaced
  them. **Provisioning a picamera node? Read §5 first** — the `picamera2`
  migration is interim, and every camera setting except `resolution` and
  `framerate` is untested and assumed broken.
- [imagenode role README](../devops/ansible/roles/imagenode/README.md) — deploying
  the outpost and the `imagenode_config` host_vars structure.
- Empirical basis for the tracker defaults (replay K-sweeps, street-cam IoU
  surveys) lives in the development design notes; the production-relevant values
  and reasoning are summarized in §1 above.
