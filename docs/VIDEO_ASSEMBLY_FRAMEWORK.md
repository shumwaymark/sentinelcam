# Video Assembly Framework

## Problem

The watchtower currently has two independent video rendering pipelines that share
most of their code:

- **VideoExporter** — on-demand single-event export triggered by the share button
- **SpeedMontage** — scheduled batch job assembling speed events into a daily montage

Both duplicate the same rendering engine, ffmpeg optimization, VPS delivery, secure
link generation, and Telegram notification. Future use cases will need all of this too.

The interesting work — what to include, how to sequence it, what story the video tells —
is different every time. The mechanical work — rendering frames with overlays, encoding,
delivering — is always the same.

## Use Cases (Known and Anticipated)

**Share button export** (exists) — User taps share on a single event. Pipeline detects
adjacent events from the same view, merges them, renders with overlays.

**Speed montage** (exists) — Daily cron job. Scans for vehicle speed events above a
threshold, brackets each with a predecessor event for visual context, renders a
compilation.

**Multi-view event sequence** — A vehicle enters the street view, approaches on the
driveway view, occupants walk past the mailbox and up the steps. Multiple cameras,
different image sizes, one continuous narrative assembled from correlated events
across views.

**Person-of-interest highlight reel** — Face recognition identifies a known person
across multiple events in a day. Assemble the clips where they appear.

**Daily activity summary** — Condensed overview of a day's events for a view. One
representative clip per event, maybe the first few seconds of each, stitched
together with date/time headers.

**Incident package** — Manual or triggered assembly of all related events around a
specific time window across all views. Everything the system saw during a 10-minute
span.

## Architecture

```
┌─────────────────────────────┐
│     Event Selectors         │  ← per use case
│  (what to include)          │
├─────────────────────────────┤
│     Sequencer               │  ← per use case (or shared default)
│  (ordering, context clips)  │
├─────────────────────────────┤
│     Video Assembly Engine   │  ← shared
│  (render, encode, deliver)  │
└─────────────────────────────┘
```

### Event Selector

Each use case provides a function that queries DataPump and returns a list of
candidate events. Input is configuration (date, view, thresholds, etc.). Output
is a list of qualifying events with metadata.

The selector doesn't care about rendering. It answers: "which events matter?"

### Sequencer

Takes the selector's output and produces an ordered list of clips to render.
This is where context bracketing (predecessor/successor events), cross-view
correlation, deduplication, and narrative ordering happen.

Output is the final render manifest: an ordered list of tuples, each describing
one segment:

```python
RenderSegment = namedtuple('RenderSegment', [
    'date',        # event date string
    'event_id',    # event UUID
    'timestamp',   # event start timestamp
    'imgsize',     # (width, height)
    'datapump',    # datapump connection string
    'label',       # optional header text override
])
```

For single-view use cases, the sequencer is trivial — just chronological ordering.
For multi-view, it handles the cross-view timeline merge and image size transitions.

### Video Assembly Engine

Shared module (`video_assembly.py`) that takes a render manifest and produces a
delivered video. Responsibilities:

- Fetch frames from DataPump(s) via DataFeed
- Decode JPEG, draw tracking overlays (all available tracking types, sorted by
  reference priority)
- Draw segment headers with adaptive text color
- Calculate FPS from frame intervals
- Write frames via cv2.VideoWriter
- ffmpeg re-encode (baseline profile, faststart)
- SCP to datasink archive
- Optional VPS upload
- Optional secure link generation + Telegram notification
- Progress reporting (callback or queue, depending on caller)

This is what `VideoExporter._render_video()` and `SpeedMontage._render_montage()`
both do today. One copy, shared.

### Shared Utilities

Extracted from the current duplicated code:

- **TextHelper** — bounding box and label drawing with per-object random colors
- **Header text color calculation** — sample frame brightness, choose black or white
- **Secure link generation** — MD5 hash with urlsafe base64 for nginx secure_link
- **Telegram messaging** — Bot API send with Markdown formatting
- **VPS upload** — SCP with timeout handling
- **ffmpeg optimization** — baseline profile re-encode with faststart

## Caller Integration

### Share Button (Interactive)

The watchtower's share button needs progress feedback for the UI overlay. The
assembly engine accepts a progress callback or queue. `VideoExporter` becomes a
thin wrapper: it handles the multiprocessing subprocess and progress queue, calls
the selector (sequential event detection), then hands the manifest to the assembly
engine.

### Scheduled Jobs (Batch)

Cron/systemd timer invocations are CLI scripts. Each use case is a standalone
entry point that reads config, runs its selector + sequencer, and calls the
assembly engine with logging output instead of a progress queue.

### Configuration

Delivery configuration (VPS credentials, Telegram, datasink paths) is shared —
either a common section in `watchtower.yaml` or a shared config file. Per-use-case
parameters (speed cutoff, view selection, time windows) live in their own config
sections.

```yaml
# watchtower.yaml additions (sketch)
video_assembly:
  local_temp_dir: /tmp/watchtower_assembly
  include_overlays: true
  header_duration_frames: 60
  adaptive_text_color: true
  delivery:
    output_dir: data1:/home/ops/sentinelcam/video_archives
    vps_upload: ...    # same as current video_export.vps_upload
    notifications: ... # same as current video_export.notifications

speed_montage:
  viewname: Front
  speed_cutoff_mph: 45.0
  schedule: daily     # informational, actual schedule is systemd timer
```

## Multi-View Considerations

The multi-view driveway-to-stoop scenario introduces:

- **Multiple datapumps** — events from different views may be on different datasinks.
  Each RenderSegment carries its own datapump address. The assembly engine maintains
  a DataFeed connection cache (SpeedMontage already does this).

- **Image size transitions** — street view might be 640x360, doorstep camera 640x480.
  The assembly engine needs a target output size. Options: largest size wins and
  smaller frames get letterboxed, or all frames resize to a configured output
  resolution. Letterboxing is simpler and preserves aspect ratios.

- **Cross-view event correlation** — the selector needs to match events across views
  by time proximity. This is a DataPump query pattern: get date indexes for multiple
  views, find overlapping or sequential time windows. The Librarian (when it exists)
  would be the natural home for this correlation logic.

- **Segment labeling** — each segment header should identify the camera view, not
  just the timestamp. The `label` field in RenderSegment handles this.

## Migration Path

1. **Extract `video_assembly.py`** from the overlapping code in `video_exporter.py`
   and `speed_montage.py`. Core rendering loop, ffmpeg, delivery, utilities.

2. **Refactor `VideoExporter`** to use the assembly engine. The subprocess and
   progress queue stay in VideoExporter. The sequential event detection becomes
   a selector function. Rendering delegates to the assembly engine.

3. **Refactor `SpeedMontage`** to use the assembly engine. Speed event scanning
   and cluster building are the selector/sequencer. Rendering and delivery delegate
   to the assembly engine.

4. **Consolidate delivery config** so VPS credentials and Telegram config are
   defined once and referenced by all use cases.

5. **Add Ansible integration** — systemd timer for scheduled montages, config
   template for the speed_montage section, deployment via existing watchtower role.

6. **Build the next use case** with the framework in place. Multi-view sequence
   is the obvious candidate — it exercises cross-view correlation and image size
   handling.

## Open Questions

- Should the assembly engine handle multi-datapump connections internally, or
  should the caller pre-fetch and pass in frame data? Internal is cleaner for the
  caller but means the engine needs DataFeed awareness. Given that DataFeed is
  lightweight and the engine already fetches frames one at a time, internal
  connection management seems right.

- For the multi-view case, how is cross-view event correlation triggered? Manual
  selection from the watchtower UI? Automatic time-window detection? A sentinel
  task that produces correlation metadata? This is really a Librarian question.

- Progress reporting: the share button needs a queue (multiprocessing boundary).
  Batch jobs just need logging. A callback interface that accepts either would
  keep the engine agnostic about its caller.
