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
        interesting_objects: [ ... ] # classes that may trigger event capture
        ROI: (10,20),(70,80)         # motion region of interest (% of frame)
        sentinel_tasks: { ... }      # §4 — post-event analysis dispatch
```

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

## See also

- [TRACKING_ARCHITECTURE.md](TRACKING_ARCHITECTURE.md) — the lifecycle, state
  machine, and two-node-types design these knobs tune.
- [imagenode role README](../devops/ansible/roles/imagenode/README.md) — deploying
  the outpost and the `imagenode_config` host_vars structure.
- Empirical basis for the tracker defaults (replay K-sweeps, street-cam IoU
  surveys) lives in the development design notes; the production-relevant values
  and reasoning are summarized in §1 above.
