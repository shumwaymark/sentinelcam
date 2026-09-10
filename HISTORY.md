# Version History and Changelog

All notable changes to the **SentinelCam** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Ongoing development

This list includes a few current lower priority, *still on the whiteboard*, design efforts.

- Continue refinements to `Outpost` implementation. 
  - Modernize object detector capabilities with support for newer algorithms, hardware.
  - Support a hybrid model selection framework for supplemental edge inference based on 
    object detection results.
- Support multiple result sets from both `Outpost` event management, and from running 
  **sentinel** tasks. Needed to support the capture of multiple neural nets producing 
  results in parallel from a single event or task. 
- Aditional refinements for the **sentinel** module. 
  - Provide an abstraction to support a reusable design pattern for ring buffer control 
    based on an object detection result filter.
  - Support a job runtime limit as a configurable setting per task engine? Provide tolerance
    based on the queue length for tasks waiting in that job class.  
- Begin to explore capitalizing on the functionality of the **librarian**  and its design 
  philosophy as a vehicle to centralize knowledge and state.


### Known bugs

- Just a general note of caution. Run this at your own risk. All major components are under 
  active development. SentinelCam is an on-going research experiment which may, at times, 
  be somewhat unstable around the edges.

## Unreleased

### Added

- **Scene retention — replay footage now expires on its own clock.** Retention had one
  granularity: an event was worth keeping or it was deleted entirely, index row, tracking
  CSVs, scene frames, and crops together. That is the wrong shape for a store whose scene
  frames are 99% of the bytes and the first thing to lose their value. Measured on the
  primary data sink: 360 GB of scene frames against 2.0 GB of crops and 604 MB of CSVs —
  the entire analytical corpus, everything the recognition work consumes, is 0.7% of the
  store.

  Scene frames are now expired independently of the event-retention verdict. An event past
  its scene window survives in full — still indexed, still carrying its tracking data and
  its high-resolution crops — it simply can no longer be replayed as video. What expires is
  the footage, not the record of what happened. Events locked as model ground truth are
  exempt and keep everything.

  The mechanism is a scoped deletion (`DataFeed.delete_event(..., scope='images')`) carried
  through the **datapump** and **camwatcher** on its own wire command, so that a data sink
  which predates the feature rejects it outright rather than reading it as a full delete.
  Policy lives with the rest of retention, per profile in the **sentinel** role's
  `DailyCleanup` task: `scene_retention_days` (absent means never, so no node changes
  behavior until it opts in) and `scene_expiry_band`, which bounds each nightly run to the
  events actually crossing the threshold. Note that `max_scan_days` must reach past the
  scene window or expiry never fires at all.

  The **watchtower** closes the loop: an event whose frames have expired now presents its
  selected-crop card over an empty backdrop, captioned as having no video, instead of a
  blank thumbnail. The crop outlives the scene by design, so the kiosk can still say what
  happened — which is the premise the whole trade rests on. Video export and the speed
  montage skip expired frames rather than writing black ones.

### Fixed

- **A wedged Coral accelerator hung a task engine indefinitely, and nothing could see it.**
  A `GetFaces` job would stop returning, the queue would back up behind it, and the Coral's
  LED would sit there steadily flashing until someone happened to check. Restarting the task
  engine always cleared it. The same signature appeared on the **outpost** with a Coral
  attached: events stop publishing, the LED flashes, and the imagenode restart takes an
  unusually long time to complete.

  `Interpreter.invoke()` is an uninterruptible blocking call into libedgetpu. Once the device
  wedges, the child process sits inside C where no Python signal handler runs and no stop flag
  is ever read — which is why the shutdown drags: SIGTERM cannot land, and systemd has to wait
  out its stop timeout and SIGKILL. There is no in-process escape from that state; killing the
  child is the only exit.

  What made it *always* `GetFaces` and never object detection was the release point. Each task
  opens the EdgeTPU in its `__init__` via `make_interpreter()`, and Python evaluates the right
  side of `task = TaskFactory(...)` before rebinding — so the previous task's interpreter was
  still holding the single-context device while the next one opened it and swapped the on-chip
  model. `GetFaces` sits second in the standard chain behind `MobileNetSSD_allFrames`, so it
  landed in that window on every event. The task is now released before the next is built.

  The **sentinel** gains a wall-clock ceiling on a running job — `job_runtime_limit`, with a
  per-task `runtime_limit` override — enforced in the job manager's service loop, where a
  standing TODO had asked for exactly this. Both existing health heuristics key off a
  *completion* event, so neither could ever observe a job that does not finish. On a breach the
  engine takes the proven restart path: the in-flight job is failed, the child killed, ring
  buffers reset, and a fresh child forked. The three detectors are now disjoint — the deadline
  owns "never finished", the strike counter owns "finished slowly", and the failure counter
  owns "errored".

- **Coral degradation scored the frame count, and restarted healthy engines.** The strike
  counter flagged any job completing with five frames or fewer as a degraded accelerator. That
  is a workload measure, not a performance one: an event with a person in view but no face
  showing for its duration legitimately yields a handful of frames at a perfectly normal rate.
  Across a retained history of 898 jobs on the coral engine the old rule flagged 34 — one at a
  single frame and 5.63 fps, another at five frames and 10.81 fps — and 26 of them were
  `GetFaces`. Only the interleaved high-count `MobileNetSSD_allFrames` jobs kept resetting the
  streak before it reached the limit, which is accidental protection rather than a rule.

  The frame *rate* was already being measured and stored alongside the count, and simply was
  not consulted. Degradation is now judged on the rate (`low_rate_threshold`), on completed
  jobs only, and only where the job carried enough frames for the rate to mean anything
  (`min_rate_frames`) — a short job's rate is dominated by fixed model-load overhead. Replayed
  against the same history the new rule flags 1 job of 898, while a simulated genuine collapse
  still trips the restart. The counter keeps its `consecutive_low_frames` name, since that is
  serialized into the state file and rendered on the watchtower's sentinel page.

- **The OAK event plane was never shut down.** Every restart of an OAK outpost logged an
  ERROR traceback — `RuntimeError('zmq gc socket requested during shutdown')` — published to
  the log plane. Harmless in effect, since the process was going away regardless, but an
  ERROR-level traceback on every restart is exactly what hides a *real* publisher fault, the
  same way the nightly purge-glob failures hid real deletion errors.

  The **outpost**'s two event-plane threads, `ScenePublisher` and `OutpostIntake`, are daemon
  threads with working `stop()` methods that nothing ever called. On SIGTERM the main thread
  ran `closeall()` and exited while the publisher kept pulling frames off the device queue and
  calling `send_jpg`, until pyzmq's garbage collector refused it a socket during interpreter
  teardown. The ordering in the log was the proof: the error landed 29 ms *after* "Exiting
  imagenode.py".

  The hook already existed — imagenode's `closeall()` calls `camera.cam.stop()` for every
  camera, which on an OAK node lands on `OAKcamera.stop()`, which was `pass`. The wiring is
  less obvious than it looks: the framework builds the `OAKcamera` shim in `Camera.__init__`
  *after* `setup_detectors` has already constructed the `Outpost` and run `setup_OAK`, so
  `setup_OAK` cannot hand the threads to the shim directly. They are registered instead on a
  class-level plane registry keyed by viewname, which the shim drains on stop.

  Teardown now runs in the only order that works — stop and join both threads, then stop the
  device pipeline, then close the sockets those threads were publishing to. `OakCamera.close()`
  had existed since the redesign with no callers at all, so the device pipeline had never been
  stopped on any restart. The scene publisher is a process-wide singleton shared across views
  and closes only once the last plane is down. A thread that misses its join budget warns and
  teardown continues rather than stalling the shutdown.

  Wiring the join exposed a latent bug that had been waiting for a caller: both thread classes
  stored their stop flag as `self._stop`, shadowing `threading.Thread._stop` — an internal
  method that `_wait_for_tstate_lock()` calls from *both* `join()` and `is_alive()`. Every
  such call against a finished thread raised `TypeError: 'Event' object is not callable`. It
  had never fired because nothing had ever joined these threads or checked their liveness. The
  flag is now `_halt`, and `ScenePublisher` additionally treats a `RuntimeError` naming
  "during shutdown" as a stop signal rather than a logged traceback, as belt and braces for a
  lost race under SIGKILL or a join timeout.

- **Missing scene frames replayed as full-screen black.** The datapump answers a missing
  image with a 1×1 placeholder rather than an error, and the **watchtower** player assigns
  each decoded frame into a ring buffer slot — where a 1×1 image *broadcasts* across the
  entire slot. An event with absent frames therefore replayed as silent full-screen black,
  raising nothing and logging nothing. Latent until now, since nothing removed frames from
  a surviving event; scene retention would have made it routine. The placeholder test the
  crop overlay already used is now applied wherever a frame is fetched, and an event with
  no images reports end-of-data instead of raising into the player's error state.

- **Crop stream liveness.** An outpost that loses power leaves the **camwatcher** crop
  subscriber holding a half-open connection: an idle SUB socket transmits nothing, so no
  FIN ever arrives and neither the kernel nor ZMQ notices the peer is gone. The rebooted
  outpost then publishes crops to a subscriber that no longer exists. The scene stream
  self-heals because its writer re-arms per event; the always-resident crop subscriber had
  given up that cycle and had no liveness check of its own. It now carries a ZMTP heartbeat
  and TCP keepalive, matching what the log subscriber already used — which is why the log
  plane survived the outage that took out the crop plane. A cross-plane watchdog backs it
  up: `crp` records arriving on the log plane with no crop file written means the crop
  socket is not delivering, whatever the cause, and the writer is re-armed (with backoff,
  and reported in the health check). The **watchtower** crop overlay now rejects the
  datapump's missing-file placeholder, so a crop that never landed reads as absent — the
  scene frame — rather than as a black card.

- **A restart shredded whatever the camwatcher was recording.** There was no signal
  handling at all, so the SIGTERM from a service restart killed the interpreter where it
  stood. The shutdown path never ran, and neither did the CSV writer's close — which is what
  actually commits a tracking file, since rows are written in text mode and none of them
  reach disk until the file closes. An event caught in flight was therefore left as a
  *zero-byte* file: its header still sitting in a buffer that died with the process, the
  file itself already truncated by the open that created it. Worse than losing the event,
  that file raises on read rather than parsing as a short one, so the damage surfaced later
  and somewhere else.

  The camwatcher now catches the signal and shuts down deliberately. An event still in
  flight gets a brief drain — long enough for the outpost to deliver its `end`, in which
  case the event closes the normal way with its post-event tasks submitted and nothing lost
  at all. Whatever is still open when the drain expires is committed short, which is the
  part that matters: a valid CSV of what was seen before the lights went out. Children are
  then reaped in order, with `CSVindex` last, since it owns the event index and every writer
  above it may still be queueing rows at it on the way out.

  This required a change to the systemd unit, and the two are a matched pair. The default
  `KillMode=control-group` signals every process in the unit at once, which would kill the
  child processes out from under the parent trying to shut them down in sequence. The unit
  now specifies `KillMode=mixed`, handing the camwatcher sole responsibility for its own
  children — so any child added later must be reaped explicitly, or it will orphan and stall
  the exit.

- **A late crop record destroyed its event's crop data.** The crop stream is not bracketed
  by start and end the way tracking is, so a crop arriving after its event had already
  closed re-originated the synthetic `crp` start — re-opening the correlation file for
  writing and truncating everything the completed event had recorded there. The crop JPEGs
  survived on disk but became unreachable through the documented join, since the records
  that addressed them were gone. Seven events in a single day on the street camera, with
  lags of five, twelve, and thirty-nine minutes between an event closing and a straggler
  arriving for it. Correlation files are now appended rather than rewritten, the header and
  index row are first-time-only work, and each crop record is flushed on arrival — it may
  never see the `end` that would otherwise close its file. Why a crop record can land
  thirty-nine minutes late is a separate question, still open, and upstream on the outpost.

- **The deployment pipeline's final stage had never run.** Package delivery and the Ansible
  sync to the ramrod node both worked, and then nothing deployed — every deployment had to
  be driven by hand from the ramrod. The pre-deployment gate invoked `ansible` from the
  calling shell's working directory rather than the Ansible home, so `ansible.cfg` was never
  read and the vault password file with it. Every connectivity probe died on the first
  encrypted group variable, and because the gate discarded standard error, a configuration
  failure was reported as unreachable nodes and misdiagnosed as a network problem for well
  over a year. The gate now sets `ANSIBLE_CONFIG` explicitly, which survives any working
  directory, and logs what actually failed.

### Changed

- **Daily cleanup retention policy is now inventory-driven.** The deployed
  `DailyCleanup.yaml` renders from `sentinel_cleanup_*` variables in the **sentinel** role,
  overridable per host, and is regenerated on every deploy. Retention is no longer a file to
  be hand-edited on the node. Includes a documented study-hold override for holding a
  complete measured-vehicle population on a street camera across a traffic study.

- **Deployments now carry configuration alongside code.** The pipeline deployed code only,
  which meant a release could land new code on a node still reading stale settings, and left
  the **sentinel** task definitions to be shipped by hand. Configuration templates and the
  sentinel task YAMLs are part of a release, not part of provisioning, and now deploy with
  the code that expects them. Systemd unit files stay excluded on purpose: they change
  rarely, and rewriting one changes restart semantics — worth doing deliberately rather than
  as a side effect of shipping a bug fix.

  This was gated on a latent hazard in the **watchtower** configuration. Its ring buffers
  are generated from the resolutions in the current outpost registry, so a camera that
  changes size silently retires the old one, and every event already recorded at that size
  loses the buffer it needs to be replayed. The running kiosk had been hand-patched to keep
  a retired resolution alive; shipping configuration automatically would have quietly undone
  that on the first deployment. Retired resolutions are now recorded in the site registry
  and rendered alongside the derived ones, so replay of historical events survives a camera
  resize. An entry can be dropped once the event data at that size has aged out.

- **A deployment now reports a verdict, not just a transcript.** A full-fleet run emits
  a couple of hundred lines of Ansible output — complete, and worth keeping, but a failed
  task or a service that quietly restarted does not stand out in it, and a component that
  changed nothing looks identical to one that did the work. An aggregate callback plugin
  now records each playbook's outcome as it runs, alongside the unchanged detailed output
  rather than in place of it, and the pipeline renders a short table at the end: per host,
  what changed, which services were restarted, and any failure with the task that caused
  it. The restart line is the one that matters, being the actual production consequence of
  a deployment. Reporting is wrapped so that it cannot itself fail a deployment.

  With that in place the pre-flight validation gate has been removed rather than kept.
  It measured the ramrod's own disk, memory and load — which say nothing about the nodes
  being deployed to — and then pre-pinged hosts that Ansible reports on a second later
  and more accurately. The one question it existed to answer is now answered better, per
  component and with a reason attached, by the summary above.

- **A sentinel engine's immunity to SIGTERM is now stated rather than inherited.** The
  graceful shutdown lets running tasks finish before state is serialized, which requires
  that its task engines outlive the stop signal — and systemd signals every process in the
  control group, not just the one it started. The engines did survive that, but only
  because they are forked after the event loop installs its own no-op signal handler and
  quietly inherit it. Forking them earlier, or reworking the signal wiring, would have
  removed the protection with no more symptom than jobs disappearing across a restart.
  The child now sets the disposition itself. Nothing behaves differently today; the
  arrangement simply no longer depends on the order two unrelated lines happen to run in.

  The drain was exercised under load for the first time in the process: eight jobs
  submitted against events carrying face-detection and recognition results, the service
  restarted with six queued and two running. Every running task was allowed to finish,
  none timed out, the queue was carried across the restart in the state checkpoint and
  ran to completion afterward, and no result file was left partially written.

## 0.3.0-alpha - 2026-06-27

The headline of this release is the **OAK outpost redesign** and the **high-resolution
crop pipeline** it establishes — a ground-truth capture path for person and vehicle
re-identification — followed by a Watchtower/health "operational readout" layer that makes
the new crop data visible and operable. See `docs/TRACKING_ARCHITECTURE.md` for the tracking
& event-management architecture overview.

### Added

- **Unified, detection-driven outpost lifecycle (OAK and picamera).** A persistent
  host-side tracker now drives event start/stop on both node types from the detection
  stream; only the detection source differs (OAK device NN every frame; picamera SpyGlass
  NN, motion-gated). Motion is demoted to an NN *scheduler* on picamera and absent entirely
  on OAK (`motion_detector: none`). A motion-only outpost remains a reserved edge case. The
  tracker, event manager, and EventSampler run in-process on a single drain thread; the only
  cross-process image handoff (selected crop → SpyGlass) reuses the proven `LensTasking`
  single-frame buffer — no new shared-memory ring buffer was introduced.

- **DepthAI v3 pipeline producing three output streams from OAK cameras.** A reduced-
  resolution 30 FPS JPEG scene stream for viewing/publishing, MobileNet-SSD detection
  metadata for tracking, and class-specific high-resolution crops produced on-device through
  a single `ImageManip` with per-config output sizing. Per-class device-side gating (throttle,
  pre/post-pad edge guards, area gate) and a host-side `EventSampler` with pluggable phase
  strategy (entry / centre / far) select ~3 well-framed crops per traversal. Five hard-won
  device-pipeline design rules are encoded in source comments at `oak_camera.py`. Requires
  `depthai>=3.5`, now pinned fleet-wide; OAK NN models deploy as NN-Archive format via the
  model registry.

- **High-resolution crop pipeline, end to end.** The outpost publishes crops on a dedicated
  ImageZMQ socket (self-identifying via a sidecar) plus a new `crp` correlation record on the
  OTE log stream. **CamWatcher** gained a resident `CropStreamWriter` (per crop-publishing
  node) that stores crops under `~/sentinelcam/crops/YYYY-MM-DD/` keyed by
  `{EventID}_{ObjID}_{Seqnum}_{Class}_{Phase}.jpg`, per-type CSV schema dispatch for the
  bbox-less `crp` records, and a CamWatcher-originated `crp` index row. **DataPump** serves
  crops via a new `get_crop_jpg(date, event, objid, seqnum, classname, phase)` request; crop
  *data*/*list* reuse the existing `get_tracking_data(..., 'crp')` path. Event deletion now
  purges the associated crop JPEGs.

- **Selected-crop kiosk overlay (Watchtower).** A new `crop_overlay.py` selects the most
  representative crop for an event — vehicle-first by VASCAR speed, otherwise the most-cropped
  subject — and composites it as an enlarged centered card over a dimmed scene with a label.
  It is raised on event-replay **pause**, on **replay completion**, and on a **new-event
  arrival** (replacing the previous sample-frame thumbnail in the outpost list and the
  live-display push). Fully additive and fail-soft: events with no usable crop fall back to
  the prior sample-frame representation.

- **Storage report crop visibility.** The nightly `storage_analysis` job now scans the
  `crops/` tree and reports `crop_count`/`crop_bytes` per view and in the disk totals; the
  Watchtower storage report surfaces crops in the SentinelCam breakdown and per-view bars.

- **Crop-publishing health.** The ramrod health observer surfaces per-OAK-node
  `crops_written` vs `crp_records` reconciliation counters into the SYSHEALTH report, and the
  Watchtower system-health page renders a crop count on the outpost card (with a delta when
  the two planes diverge — a glanceable pair-failure signal).

### Changed

- **VehicleSpeed now keys on the persistent HostTracker identity.** The task groups vehicle
  detections by the `trk` objid (the persistent tid resolved upstream at the outpost) and
  walks each vehicle's trajectory, rather than re-associating detections with its own
  in-task centroid tracker. Result: `vsp.objid == trk.objid == crp.objid` — the identity
  join the re-ID pipeline is built on. The replay overlay label is now speed-only
  (direction dropped — it is recoverable from the bbox trajectory).

- **Storage report relocated** from the buried "tools" settings page to the System Health
  page, reached via the DataPump tile.

- **Outpost confidence floor.** A tracker birth-gate (`min_confidence_new: 0.60`) eliminates
  low-confidence detection flicker (validated on the east street camera).

### Fixed

- **VehicleSpeed label swaps and parked-car attribution bleed** — eliminated at the source by
  keying on the persistent tid instead of re-tracking identity from scratch.
- **Crop ImageManip config lag** — the on-device crop manip applied the previous emit's
  config (wrong geometry under multi-subject coexistence); fixed with one-config-per-image on
  `inputConfig` (device-pipeline Rule 5).
- **Bastion WireGuard clock deadlock after power loss** — the tunnel could not recover when the
  RTC-less bastion's clock reset to the past and the VPS dropped handshakes as replays; fixed
  with chrony-via-VPS, waitsync, and a watchdog self-heal.
- **Watchtower replay/export overlays** now skip non-geometry tracking types (e.g. `crp`),
  which previously crashed the per-frame overlay render with NaN rects.
- **Dangling `deploy-ramrod.yaml` reference** in the deployment config repointed at the real
  (config-only) `configure-ramrod.yaml`; ramrod code ships by rsync, not a playbook.

### Removed

- **Legacy correlation-tracking cascade** on the outpost — the `Lens_DETECT/TRACK/REDETECT/RESET`
  cascade, dlib/OpenCV correlation trackers, SpyGlass scene-management surface, `skip_factor`,
  the Active/Quiet/Inactive state machine, and the 15-second kill switch. Replaced by the
  unified tracker-driven lifecycle and a structured `Detection` contract shared by the OAK and
  picamera intakes.


## 0.2.8-alpha - 2026-05-25

### Added

- Added `SpeedMontage`, a daily speed event montage generator for the **watchtower**.
  Scans a single day's event index for `vsp` tracking records from a configured outpost
  view, selects events exceeding a configurable MPH cutoff, and brackets each qualifying
  speed event with the immediately preceding and following `trk` events to provide visual
  contrast. The full cluster is rendered as an ffmpeg-encoded MP4 with bounding box overlays
  and per-segment header frames. The output is optimized for web streaming, copied to the
  datasink, and optionally delivered to the VPS with a Telegram notification — following
  the same sharing protocol as the on-demand video export introduced in 0.2.4-alpha.
  Intended to be driven by a daily systemd timer targeting the prior day's data.

- Added a WireGuard watchdog to the **bastion** role. A systemd timer periodically inspects
  the handshake age of all configured WireGuard peers and restarts the tunnel interface if
  any peer is stale or has never completed a handshake. This recovers from post-power-failure
  conditions where the WireGuard interface appears up but the tunnel is unresponsive — a
  failure mode that previously required manual intervention on the bastion host.


## 0.2.7-alpha - 2026-04-19

### Added

- Added the **ramrod** health observer service. A lightweight cron-driven process (every 3 minutes) 
  that queries **camwatcher**, **datapump**, and **sentinel** for health check responses, consolidates 
  the results into a single `SYSHEALTH` report, and delivers it to the **sentinel** for re-broadcast 
  to all log subscribers. Each invocation is stateless. The report includes per-outpost status (writer 
  liveness, heartbeat age, FPS, stale detection), pipeline service health (CamWatcher disk and agent 
  status, DataPump request metrics, Sentinel engine and queue state), and a top-level ok/degraded flag. 
  Deployed via Ansible with its own role, systemd timer, and configuration template.
- Added a system health display to the **watchtower**. A heart-shaped status icon on the player page 
  reflects overall system health: green for nominal, amber for degraded, red with a pulsing animation 
  for critical. The heart shows and hides with the player button bar normally, but in alarm mode (any 
  service or outpost down) it remains persistently visible even after buttons auto-hide. Tapping the 
  heart navigates to a new `SystemHealthPage` displaying outpost status cards, pipeline service cards 
  (CamWatcher, DataPump, Sentinel), a ramrod liveness age indicator, and an alert feed. The heart icon 
  frames are pre-computed at startup as PhotoImage objects to avoid per-frame allocation. Visiting the 
  health page clears the alarm flag; heart color continues to reflect actual state. The Sentinel card 
  now opens a performance drill-down page with Live, Charts, and History panels for engine status, 
  queue depth, ring latency, recent job completions, multi-hour trend plots, and historical bottleneck
  snapshots. Optional display thresholds are configurable via watchtower.yaml.

### Fixed

- Fixed a protocol mismatch between the **datapump** and `DataFeed` that caused an infinite 
  thread crash-restart loop in watchtower and sentinel DataFeed clients. When the DataPump 
  encountered an error processing a request (missing field, unexpected exception), it sent a 
  single-frame reply (`b'Error'`) via `send_reply()`. But the DataFeed's `_cmdloop` dispatched to 
  format-specific receivers (`recv_DataFrame`, `recv_pickle`) expecting multi-part messages — the 
  first `recv_json()` call would receive the raw error bytes, fail with `JSONDecodeError`, and crash 
  the thread. The timeout handler would then recreate the socket and spawn a new thread, which 
  immediately hit the same error on the next request — producing the repeating ~15-second crash cycle 
  visible in the journal. Fixed on both sides: the DataPump now sends error responses in the wire 
  format matching the command type (DataFrame, pickle, or jpg), and the DataFeed `_cmdloop` catches 
  recv exceptions, drains any remaining multipart frames, and signals the error to `pump_action()` 
  which raises `ConnectionError` — keeping the thread alive for subsequent commands.


## 0.2.6-alpha - 2026-03-21

### Added

- Added internal task engine health heuristics to the **sentinel**. Each engine now tracks ring buffer 
  latency, consecutive failure counts, and a low-frame completion pattern specific to Google Coral USB 
  accelerator degradation — where jobs technically "complete" having processed almost no frames. When an 
  engine triggers the strike limit for repeated low-frame completions, the `JobManager` initiates a 
  proactive restart with an enforced cooldown period. Ring latency is measured child-side to capture the 
  true round-trip stall, including time the request sat unnoticed while the `JobManager` was servicing 
  other engines. Configurable thresholds for strike limits, cooldown intervals, and lifetime restart caps.
- Meaningful health check responses added to **camwatcher**, **datapump**, and **sentinel**. The 
  `STATUS` command on the sentinel, previously a stub, now returns a substantive JSON snapshot of engine 
  state, queue depth, health counters, and per-engine ring buffer latency statistics. This required 
  solving the thread-safe snapshot problem: the data lives in the `JobManager` thread, but requests 
  arrive on the async event loop. A `SnapshotRequest` pattern routes requests through the existing 
  `taskFeed` queue with a `threading.Event` for synchronization — consistent with the established 
  architecture where all state mutations flow through that single queue. CamWatcher and DataPump health 
  checks similarly report on child process liveness, frame counts, outpost heartbeat status, and 
  request volume. The outpost heartbeat tracker in CamWatcher monitors the 5-minute heartbeat interval 
  and flags missed heartbeats.
- Added queue diagnostics instrumentation to the **sentinel**. Beyond a simple depth counter, this 
  captures high-water mark context snapshots at the moment each peak occurs, per-class queue depth 
  tracking, a rolling 5-minute submission rate window, and per-engine utilization as cumulative busy 
  time. These accumulators feed both the STATUS response and the daily maintenance summaries, providing 
  the operational data needed to understand queue behavior under load.
- Added a daily maintenance cycle to the **sentinel**. A `MAINTENANCE` control command, triggered by 
  a systemd timer at 01:00, runs within the `JobManager` thread to compute daily summaries by engine 
  and task class, publish HEALTH-flagged records to the log transport, reset diagnostic accumulators 
  for the new period, checkpoint state to disk, and trim completed job records past the retention 
  window. This reuses the `SnapshotRequest` mechanism established by the STATUS command.
- Added state serialization and startup recovery to the **sentinel**. A `sentinel_state` module 
  provides atomic file writes (temp file + rename) for checkpoint persistence of the job dictionary, 
  queue state, and engine health counters. The state file is written during daily maintenance and 
  incrementally every 50 job completions, bounding crash recovery loss to roughly 30 minutes of 
  history. On startup, the sentinel restores job history for the HISTORY command and seeds engine 
  health counters. Queue recovery from stale checkpoints is intentionally limited — jobs likely 
  completed normally after the checkpoint was taken. Full queue preservation is deferred to the 
  graceful shutdown work.
- The `SentinelAgent` in the **camwatcher** now captures HEALTH-flagged records from sentinel log 
  publishing, writing them as JSONL files on the data sink. This provides the persistent historical 
  health record, organized by date, that will later be served through the **datapump** for operational 
  dashboard use.
- Added custom desktop wallpaper to the **watchtower** kiosk.
- Added graceful shutdown to the **sentinel**. A `SHUTDOWN` control command initiates an orderly
  drain: the `JobManager` stops ventilating new jobs to engines while continuing to service ring
  buffers for tasks already running. Once all engines go idle (or a configurable timeout expires),
  the full job dictionary, queue state, and engine health counters are serialized to disk. Timed-out
  jobs are reset to Queued and re-execute from frame 1 on the next startup — input data on the
  datasink is immutable, so no work is lost. SIGTERM triggers the same sequence, so `systemctl stop`,
  `systemctl restart`, and Ansible-triggered deploys all preserve state automatically.

### Fixed

- Configured TCP keepalives, with generously patient reconnects, on the **camwatcher** async SUB 
  sockets.

### Changed

- Jobs are now placed on-deck by task engine rather than by job class in the **sentinel**. The previous 
  approach maintained a per-class on-deck slot, which could leave engines idle when multiple jobs 
  arrived for classes already represented on-deck. Placing on-deck by engine is simpler and avoids the 
  imbalance conditions previously witnessed across a single job class.
- Motion detector calibration tool now includes ROI settings.

## 0.2.5-alpha - 2026-02-22

### Added

- Added `TaskEngine.restart()` to the **sentinel** to recover from child process failures without 
  restarting the entire service. The critical design point is that shared memory stays allocated across 
  the restart — only the child process is torn down and re-forked, reusing existing ring buffer 
  references. The `JobManager` detects dead engines automatically and triggers restart with a 
  configurable failure limit per engine before permanent removal from the pool. Successful completions 
  reset the failure counter. A `RESTART_ENGINE` control command is also available for manual 
  intervention. This also fixed a companion bug where `TaskBOMB` messages from failed engines were 
  being silently dropped due to an incorrect `taskFeed.put()` call — bomb messages now properly reach 
  the `JobManager` for cleanup.
- Replaced the flat hourly time-slot event list in the **watchtower** with a full calendar-based event 
  history browser. A month-view grid shows event density per day with color-coded cells indicating 
  activity levels. Tapping a day drills down to hourly time slots via a scrollable touch-friendly menu. 
  Month navigation is bounded by the earliest and latest dates in the event cache.
- Added a `HistoryLoader` subprocess to the **watchtower** that populates multi-day event history from 
  the **datapump** at startup. Today's events load first for immediate responsiveness; the subprocess 
  then walks backward through all available dates in the background. The kiosk is usable right away 
  while older history fills in progressively. A per-view `max_events` cap limits memory consumption. 
  This must be a subprocess rather than a thread because `DataFeed` creates ZMQ sockets that cannot 
  safely cross a fork boundary.
- Added a storage analysis reporting pipeline. A `storage_analysis` module on the **datapump** walks the 
  sentinelcam filesystem to produce a pre-computed report of disk capacity, daily image intake, and 
  per-view breakdowns. A new `StoragePage` on the **watchtower** presents the results in a three-level 
  drill-down: sink overview with capacity gauge and runway estimate, daily intake as a bar chart, and 
  per-camera breakdown. Designed to run nightly via systemd timer after `DailyCleanup`. This is a 
  read-only reporting tool — it does not delete anything.

### Changed

- Redesigned the motion calibration tool in the **watchtower** as a player overlay instead of an 
  independent viewer. The original design created its own `ImageSubscriber` alongside the 
  `PlayerDaemon`, but ZMQ PUB/SUB distributes messages round-robin across multiple subscribers on the 
  same endpoint — each saw roughly half the frames, producing choppy feeds for both. Calibration now 
  receives frames through the existing player pipeline. This establishes the standard integration 
  pattern for future feature modules: use the player's frame dispatch, never create an independent 
  subscriber.

### Fixed

- Addressed a state machine deadlock in the **watchtower** where rapid button presses during event 
  browsing could lock the UI, requiring a service restart. The root cause was synchronous blocking 
  calls on the main Tk event thread — unbounded queue waits and idle event waits that could hang 
  indefinitely when rapid toggles caused acknowledgment mismatch between start and stop sequences. 
  Added timeouts to all blocking interactions, converting potential hangs into recoverable error 
  states, with a re-entrant guard on TOGGLE processing as defense-in-depth.
- Corrected an auto-advance defect in the **watchtower** where the `PlayerDaemon` would wedge during 
  sequential event playback. The issue was that state transitions were being queued with compensating 
  commands based on assumed state, but by the time they processed, the state had already changed — 
  leaving the daemon running but deaf to commands. The fix moves daemon lifecycle management to 
  processing time when the actual state is known. Also unified event position tracking so auto-advance 
  and manual navigation share the same index.


## 0.2.4-alpha - 2026-01-17

### Added

- Added a **sentinel** task for vehicle speed estimation by VASCAR calculations. Intended for an
  outpost deployment with a view of the street where vehicle traffic is passing horizontally across
  the field of view. See the `VehicleSpeed.yaml` task configuration file for details. 
- While browsing event history, the share button on the **watchtower** will now produce an on-demand 
  video export of captured event data and make it available for externally sharing via the following
  protocol:

    - An outpost node will generally capture event data only while there is motion.
    - At times, a single larger event might be comprised of multiple event captures due to intermittent pauses in activity.
    - The configuration file includes a setting for the maximum elapsed time between captures for individual segments to be considered part of the larger event, i.e., 30 seconds. 
    - There is also a configurable upper limit on the number of sequential captures to be stitched together into a single video export.
    - An MP4 video format export is produced of the event data complete with labeling and bounding box overlays from model inference results.
    - A date and time heading is briefly included as labeling along the top of the video as each segment is introduced.
    - The export is optimized for streaming.
    - A copy of the MP4 file is dropped in a folder on the primary data sink.
    - This is currently unmanaaged, an item for the To-Do list.
    - The Sentinelcam network is secured by a bastion host connected to a public-facing VPS via a WireGuard tunnel.
    - The tunnel is on private a 10.0.0.0/24 network segment, and an operational SSH key for the VPS is deployed to the watchtower nodes.
    - A simple `scp` command is employed to drop a copy of the MP4 file into a folder on the virtual private server.
    - The VPS is running *Nginx* with a public-facing address.
    - *Nginx* supports a secured link, containing an MD5-encrypted hash, which expires based on time. 
    - A secure link to the uploaded file is constructed and delivered to a *Telegram* bot in a Markdown-formatted message. 
    - This link can be shared with anyone.
    - If preservation is desired, the recipient will need to download and save the video prior to its expiration time.
    - A scheduled clean-up task on the VPS deletes expired videos from the upload folder. 
   
- Added a motion detector calibration tool to the **watchtower**. Though functional, this is still coming
  together. Based on the currently selected outpost node, the live current camera view is displayed. A set 
  of controls are also presented which allow for manipulating parameters supporting the motion detector. As
  parameters are adjusted, changes to motion detection sensitity can be evaluated in real time. There is 
  currently no facility to apply the new settings back to the outpost, nor does the tool open with the
  current settings. Also for the to To Do list, this same control should allow for reviewing the ROI (region 
  of interest) settings visually, adjust them as desired, and save back to the outpost configuration.
- Added a general purpose alert re-broadcast mechanism to the **sentinel**. Initially implemented
  for the **camwatcher** to send out event deletion alerts. The **watchtower** needeed this message
  to prune stale data from its internal event cache. This was built as a general purpose alerting 
  mechanism which could be exploited to allow an outpost node to produce a system-wide alert for
  re-broadcast by the **sentinel** log publisher. This information would then be available to the
  **watchtower**, or any interested subscriber, in real time.

### Changed

- The **imagenode** configuration for outpost nodes now support an `interesting_objects` setting, which 
  is a list of classnames from the object detection neural net. This expands on the original hard-coded
  value of `['person']` as the single class of interesting objects. This is used in event retention and 
  directing analysis tasking for the **sentinel** pipeline at the end of event capture.
- Simplified Python dependency mangmement. Moved standard `requirements.txt` into a file in the
  `sentinelcam_base` role rather than maintaining a file system object in the source repository. This 
  can now be easily overriden at the host level as needed.
- Expand on the `DailyCleanup.yaml` task logic and configuration for the **sentinel** into the beginnings
  of a rules-based data retention policy. This now supports data policy by tracking type, retention length, 
  and categorization by outpost node. This is still evolving.
- Motion detection parameters moved into the **imagenode** application configuration file.
- The **watchtower** nodes are now configured as kiosks with an always-on display. These nodes subscribe to
  **sentinel** log publishing, watching for task results. As new events arrive, a sample image is selected
  based on inference results available at that time. Labeling and bounding box overlays from neural net
  outputs are drawn on the image, which is then displayed on the viewer and used as the thumbnail 
  in the list of outpost nodes. The current view selection is also automatically changed to whichever outpost
  produced the event. Tapping on the play button at that point will provide a live camera view from the
  outpost. Tapping on the previous button will replay the event which was shown on the viewer.
- An event history list was added to the **watchtower**, which is tracked internally by outpost view. Due 
  to the potentially large number of events which can occur, the menu allows selection by hourly time slot.
  One a starting time is selected, the user can begin replay or browse forward and back as desired.

### Fixed

- Auto-advance functionality for the **watchtower** event reviewer fleshed out and completed. Tapping on the
  previous button will replay the previous event and stop at the end of it. The play/pause button can then be
  used to replay that event multiple times. Tapping on the next button implies an auto-advance. The player
  will then begin playing each subsequent event in sequence. When the last event is reached, the player
  transistions into a live camera view. During replay, tapping any other button will stop the auto-advance.

## 0.2.3-alpha - 2026-01-02

### Fixed

- Addressed gaps in DNS configuration when adding or replacing network nodes. 

### Added

- Initial support for using BlazeFace from Google Mediapipe for face detection. Only the short
  range model seems to be publicly available. This is currently more suited to a bespoke outpost 
  deployment intended to collect selfie-style images, such as a door camera. The existing face detection
  task on the sentinel, with Google Coral support, was modified to use this. So, more of an edge case as 
  implemented. Categorized as forward-looking and a low-priority development item at this time. 

## 0.2.2-alpha - 2025-12-31

### Added

- Moved DepthAI model file name into application configuration, and incorporate into the model
  registry and devops playbooks.

## 0.2.1-alpha - 2025-12-30

### Added

- Full support for Google Coral USB Accelerator, on both the outposts and sentinels. This includes 
  software package provisioning, application configuration, as well as model registry and deployment 
  for `mobilenet_ssd_edgetpu` and `face_detection_edgetpu`. Support for face detection on the outposts, 
  as a supplementary lens for the `SpyGlass`, is an outstanding To Do List item.

### Changed

- Code and model deployment tasks now pull from the repositories on the primary data sink directly to each 
  target node via locally executed `rsync` commands. An operational SSH key is stored as a variable in
  an Ansible encrypted vault for deployment and use by target nodes. See `playbooks/deploy-ssh-key.yaml` 
  for details.

## 0.2.0-alpha - 2025-12-09

### Added

- *Complete DevOps infrastructure*: Full Ansible-based CI/CD pipeline and configuration management
  system. A comprehensive automation framework for server provisioning, software deployment, configuration, 
  and maintenance of all system components:
  
  - Role-based architecture definitions for all major components including sentinel, imagenode, imagehub, 
    camwatcher, datapump, watchtower, and supporting infrastructure roles: base provisioning, configuration, 
    deployment, and management
  - Centralized configuration management as a single source of truth for paths, standards, ports, and
    deployment patterns (`sentinelcam_standards.yaml`). Data-driven component deployment with automatic
    service discovery and registration
  - Code deployment pipeline as an Rsync-based code distribution from primary datasink to all nodes
    with integrity checking, service restart coordination, and rollback capability. Automated handling
    of server provisioning, Python virtual environments and dependencies
  - Network infrastructure management for SSH key distribution, bastion host integration, static IP
    configuration, and automated network interface setup across heterogeneous hardware
    
- *Model registry and versioned deployment system*: Complete infrastructure for managing ML models
  as a first-class concern, separate from code deployments:
  
  - Centralized model registry on primary datasink with YYYY-MM-DD timestamp versioning
  - Dedicated deployment playbook (`deploy-models.yaml`) with pre-deployment validation and SHA256
    checksum verification for model integrity
  - Configuration-driven rollback capability where models persist on disk while only configs change,
    enabling instant version switching without redeployment
  - Automated cleanup via systemd timer with configurable retention policies
  - Full integration with ML training pipeline including automated model upload, manifest generation, 
    registry updates, and triggered deployment
  - Interactive rollback utility script with version history, validation checks, and confirmation prompts
  - Template-based server configurations with dynamic model version injection and per-host override support

### Fixed

- Addressed missing exception handling in the **watchtower** player daemon subprocess, including purposeful
  coordination with player state.

## 0.1.4-alpha - 2025-05-12

### Fixed

- Added debug logging to the **watchtower**.
- Tightened the screws on the **watchtower** state machine. Implemented a message passing mechanism between 
  application control and the player subsystem, coordinated through a centralized state manager. No further 
  leaks detected.

## 0.1.3-alpha - 2025-04-24

### Fixed

- Hardening within the **sentinel** job manager. Previous changes to task engine job
  management were not properly tracking all state changes, leaving jobs in the queue
  unprocessed and task engines apparently hung. 
- Further work on the **watchtower** state machine. The player thread now also caches 
  event result data for efficiency.

## 0.1.2-alpha - 2025-04-21

### Fixed

- Resolved play/pause synchronization issues in **watchtower** with full state acknowledgment 
  between player daemon, player thread and UI components. Cleaned up event list management and 
  event selection logic. Added a configurable option to limit the event history to a specific
  number of events per view.
- Refactored the `ImageSubscriber` class as a proper subclass of `imagezmq.ImageHub`, along with 
  hardening for error handling and retries on connection failures. Support safe disconnects
  from publishers. 
- Refinements to **sentinel** task engine for more efficient and robust job scheduling, including 
  corrections to the `JobManager` class for better ventilation and task list management. 

## 0.1.1-alpha - 2025-03-02

### Deprecated

- Real time *OpenCV* correlation tracking from the **outpost**, as currently implemented, to be 
  removed for a future release. At this level, any `SpyGlass` correlation tracking should be refactored 
  and based solely on geometric centroids of objects detected across result sets. Some newer detection 
  models support this directly as a factor within the output tensor. 

### Changed

- Improved stats collection from the `FaceRecon` task, which now include the missing facial distance 
  results. The `FaceSweep` task considers these factors when selecting candidate images for modeling
  purposes. 
- Produce a standard `ImageSubscriber` class for subscribing to **outpost** image publishers. This
  supports a load-and-stay-resident use pattern with `start()`, `stop()`, and `subscribe()` methods
  allowing a publication stream to be paused and restarted, and changed from one node to another.
- The `Task.finalze()` method of the **sentinel** task factory now supports a boolean return value.
  A false result indicates that any chained task should not be executed. For example, there is no need
  to invoke a downstream facial recon task when no faces were detected in the result set. 

### Fixed

- Added a background process to the **watchtower** for maintaining a list of events. Work to eliminate 
  Tk thread abuse, by not calling into Tk from the background. This new module is almost stable for its 
  base use cased of switching between current camera views and replaying prior events. Some issues remain. 
  Additional work continues towards a stable touchscreen wall console. 
- Further work towards the pursuit of iron-clad resiliency around timeout handling for failed and/or
  unresponsive communication connections.
- Flag waving and declarations of war from the campaign to eliminate abuse of the 0MQ layer. Battles 
  fought and won. A happy road paved over the carnage.

## 0.1.0-alpha - 2025-02-12

This push marks the start of the migration to Raspberry Pi OS, *Debian 12 (bookworm)*. 
Now employing Python 3.11 and the `picamera2` library for camera image captures. Additional
work towards this effort is still ongoing. 

### Fixed

- Corrected prior repair to the `DataFeed` for unresponsive connections to now properly close,
  then allocate a new 0MQ `Socket` instance for reconnecting to the **datapump**. This often
  lives in multi-threaded applications where more than a single `DataFeed` instance may be 
  active. Avoids unwarranted destruction of the `Context`. Which could bring the roof down.
- Moved **camwatcher** index updates into a separate chid subprocess to prevent index corruption.
  Single threading is needed for this task since mulitple events can be initiated simultaneously, 
  while new **sentinel** task results could also be arriving in parallel. This new subprocess now
  manages all event deletion also. The **datapump** delegates event deletion through this same
  choke point via a command sent to the **camwatcher** control port.
- Removed **outpost** image publication throttling logic based on elapsed time. This was an unreliable 
  approach since that measurement can vary significantly from one cycle tick to another. The correct 
  solution here was to abandon use of the threaded read logic in favor of direct image retrieval through 
  the `picamera2` library. This resulted in close to ideal throughput and dramatically reduced CPU load.
- Tossed a floaty into the **outpost** *DepthAI event-trigger-whirlpool-of-death*. Didn't realize it
  couldn't swim. Should've known. It was never properly introduced to deep water.

### Changed

- Moved post-event trigger to **outpost** configuration as a list of tasks for the **sentinel**.
  Multiple tasks are supported based on object detection results, these run with job priority=1. 
  An optional `default` task, submitted as priority=2, can be specified as a catch-all to always 
  run at the end of each event.
- Moved logging configuration into the application YAML setup files for each component.
- A quick-and-dirty hack on `PiCameraUnthreadedStream` within the **imagenode** to use the `picamera2` 
  library. Legacy configuration options to support camera settings such as exposure, contrast, shutter
  speed, white balance, etc. were all implemented with the original `picamera` library, and are abandoned 
  by this shortcut. 

### Added

- Added support for using a *Google Coral USB Accelerator* with the **sentinel**. Activated When the 
  task engine is configured for Coral, this adds support for using the `edgetpu` library for specifying 
  *TensorFlow Lite* models for both object detection and face detection. 
- Added a job priority field to the **sentinel** task list. Post-event tasks initiated from real time
  **outpost** analysis are assigned a priority 1. Chained jobs receive the same priority as the prior 
  job in the chain. Other analytical tasks are assigned the default priority of 2. The `JobManager` 
  will attempt to place tasks on-deck by priority.

## 0.0.33-alpha - 2024-10-01

### Fixed

- Vaccinations against zombie subscriber syndrome.
- Cleaned up **sentinel** job history dump.
- Correct tracking timestamps published for **outpost** *DepthAI* pipelines.
- Addressed oversleeping in the **sentinel** Task Engines and Job Monitor thread.

### Changed

- Include face recon status as criterion in **sentinel** `DailyCleanup` task. See explanation in
  the YAML configuration file within the Tasks folder.
- Refactored **sentinel** task chaining logic for efficiency. Execute chained tasks immediately on
  the same engine if the target job class is supported. 

### Added

- Added systemd timer setups for daily maintenance **sentinel** task.

## 0.0.32-alpha - 2024-09-16

### Fixed

- Moved results selection and sorting for each frame outisde the **watchtower** event review loop.
  This allowed for reaching expected performance goals, and required the reintroduction of a delay 
  between frames to slow down the replay so that it aligns with the capture rate.
- The **watchtower** now selects an appropriate white or black text color for result labels based 
  on a lumninace factor of the randomly selected background color assigned to each distinct item.

### Changed

- A complete refactoring of the `DataFeed` for conciseness and clarity. Will now raise a `TimeoutError` 
  exception for an unresponsive **datapump** connection, including a close and reconnect on the 0MQ socket.
- Change from UTC to localtime for all timestamps.

## 0.0.31-alpha - 2024-09-02

### Fixed

- Bug sweep on the **watchtower** wall console. Working now. Has core functionality in place 
  for both live viewing, and previous event display.

## 0.0.30-alpha - 2024-08-31

### Fixed

- Clean-up on the **watchtower** wall console. Has 99.94% of the core functionality needed for 
  both live viewing, and previous event display.

### Changed

- Revised EOJ status message from **sentinel** for event syncrhonization with the **watchtower**.

## 0.0.29-alpha - 2024-06-18

### Changed

- Another premature push. Untested work-in-progress on the **watchtower** wall console. This
  has nearly, ~80%, of all the core functionality required for its primary use cases. The full 
  feature list is still only conceptual at this point. 

## 0.0.28-alpha - 2024-06-11

### Added

- An extremely early push, for safe keeping, of the **watchtower** wall console. Designed
  for the Raspberry Pi 7-inch touchscreen display, this is a combination live outpost viewer
  and prior event display tool showing image analysis results. *Though a working proof of
  concept, this barely qualifies as a prototype; just a little buggy and critical funcionality 
  is missing*.

## 0.0.27-alpha - 2024-06-11

### Changed

- The `DataFeed` now raises a `ImageSetEmpty` exception when requesting the image list and
  no images were captured for the requested event.

## 0.0.26-alpha - 2023-12-22

### Added

- Defined `FaceList` class to encapsulate access and updates against the `facelist.csv` file of
  faces which are in use or awaiting analysis. Storage is in a CSV-format text file used to support
  management of the facial recognition and learning pipeline. This also serves as an event lock 
  preventing data deletion whenever present within the dataset.
- Added `FaceSweep` and `FaceDataUpdate` task definitions to flesh out the facial recognition and
  learning pipeline. The former identifies new candidate images to be considered for inclusion in the 
  next model update. The latter writes selected candidates into the `facedata.hdf5` file of embeddings
  used for modeling.

### Fixed

- Cleanup and shakedown of facial recon pipeline.
- Corrected **sentinel** ring buffer loading when task is configured with `ringctrl: trk`. Now loads
  only the subset of unique images for the tracking type. 
- Delay clearing of **sentinel** on-deck presence until task has either reported a successful start
  or failed during initialization.

## 0.0.25-alpha - 2023-12-04

### Added

- The **sentinel** now supports easily configurable pipeline definitions, through task chaining and
  aliasing. This is managed by new `Task` attributes in the YAML file.
- Defined an `EventList` class to encapsulate the most common event selection and processing methods:
  by date or date range, by specific date and event, and from a from text file with a list of events 
  to process. An optional tracking type parameter is supported to refine the selection. 

### Changed

- Facial reconnaissance pipeline now incorprates Euclidean distance metrics against the individual 
  baselines kept for known individuals. **OpenFace** embeddings are used for both this purpose and also
  for training the SVM classifier. This ensemble approach both bolsters classification results and 
  helps address the open set recognition problem inherent in the overall design. 
- The `DataFeed` now raises a `TrackingSetEmpty` exception when attempting to retrieve tracking data
  that does not exist. 

### Fixed

- Include support for carrying object ID references within tracking data updates from tasks running 
  on the **sentinel**.
- Now properly reporting messages with ERROR and WARNING logging levels from tasks running on 
  the **sentinel**. Previously, these were being logged with a level of INFO.

## 0.0.24-alpha - 2023-10-29

### Added

- Include alpha version of facial reconnaissance pipeline.

### Fixed

- Send empty image from **datapump** when size of JPEG file is zero.
- Corrected tracking references for **camwatcher** updates when task ringctrl is ``trk``.

## 0.0.23-alpha - 2023-05-03

### Changed

- Event index modified to include captured camera image dimensions (width, height). This is helpful 
  for buffer allocations during downstream processing.
- Support an alternate image cursor for populating the **sentinel** ring buffer. This allows image
  retrieval to be restricted to only images included in a specific result set. The default for this is 
  to provide all images captured for an event.

### Fixed

- Exit the **sentinel** task gracefully whenever image retrieval fails. 
- The **sentinel** on-deck status was not being properly cleared for jobs running in secondary classes. 

## 0.0.22-alpha - 2023-04-17

### Added

- Face detection pipeline introduced. This demonstrates a **sentinel** task designed to 
  run against just a subset of event images. Skip-ahead logic is used to advance the ring buffer
  start dynamically, so that only frames with a previously detected "person" object are analyzed.

### Changed

- Corrected timestamp on logged `SpyGlass` results to match timestamp of frame being analyzed. 
  This was previously being stamped with the time results were received, resulting in an 
  noticeable lag. Bounding boxes were sometimes being drawn behind moving objects, following 
  them like some kind of ghostly electronic shadow. 
- Support an event type selection as a part of **sentinel** task configuration. Each task 
  receives a set of tracking data, which defaults to `'trk'`. This change allows tasks to either 
  process every frame in the event, or selectively analyze only a subset of frames based on results
  in a previously collected dataset.
- Added basic task performance instrumentation to the **sentinel** end-of-job message.

### Fixed

- Corrected alignment logic between results and images when presented for video review. Factoring
  in estimations around elapsed time within the event has not been helpful.
- Timestamp mapping for **sentinel** tasks was incorrectly based on the first `trk` record, rather 
  than the first frame. 
- Fixed a bug in how the `JobManager` for the **sentinel** manages the task list. Failed task initialization
  could sometimes lead into a spiral of death and destruction. 
- The **sentinel** was occasionally attempting to feed a ring buffer no longer in use, when a task had
  selected an early exit. This exposed a bug where ring buffer operations were being executed against tasks 
  just ending, resulting in failures.  

## 0.0.21-alpha - 2023-04-06

### Changed

- Spit and polish for *camwatcher v3* support. Bug clean-up sweep.
- Minor updates to `video_review_df.py` for selecting alternate result sets. 

## 0.0.20-alpha - 2023-03-27

### Changed

- Restructured python module organization for the data sink codebase.
- Fleshed out **camwatcher** setups and migrated into a YAML document. 
- Child process image subscribers are now pre-loaded when the **camwatcher** initializes.
  These are kept resident between events for faster response to new activity.
- A list of known `Outpost` nodes has been added to the **camwatcher** settings. Subscriptions
  to these are established automatically at startup. A new camera node can still introduce 
  itself dynamically. This change allows the **camwatcher** and **imagenode** applications to 
  restart independently of each other. Previously, all camera nodes had to be restarted 
  whenever the **camwatcher** was bounced. 
- Now providing **sentinel** task results via 0MQ log publishing. This content includes analysis 
  results and status messages, along with internal errors and warnings. The **sentinel** does not
  write to a logfile on local disk. All logging is published for any interested subscribers.
- A subprocess agent was added to the **camwatcher** for subscribing to **sentinel** logging messages
  and capturing task analysis data. 
- Post-event processing logic was added to the **camwatcher**. This is used to automatically submit a 
  parameterized task to the **sentinel** for a complete analysis of all event data. Results are stored
  as a supplement to the original captured tracking data provided by the `Outpost` nodes.

## 0.0.19-alpha - 2023-03-22

### Fixed

- More hardening in the data layer. Improved exception handling, with a more graceful failover from 
  the **datapump** to a `DataFeed` requester. 

## 0.0.18-alpha - 2023-03-21

### Fixed

- Data layer resilience. `CamData` class was failing when event detail CSV files were missing. 
  Now properly returns an empty `pandas.DataFrame` for this condition.
- Additional tightening of the **sentinel** for efficiency and stability. Fixed a bug in the
  start logic when task has no eventID.

## 0.0.17-alpha - 2023-03-18

### Fixed

- Provide support for **sentinel** task engines to have complete control over the ring buffer,
  including issuing start commands, and changing context to a new event. 

## 0.0.16-alpha - 2023-03-17

### Fixed

- Stress testing the **sentinel** module with multiple task engines. Support a ring buffer
  model for customization by task engine if desired. Confirm affinity to task engine by job class.

## 0.0.15-alpha - 2023-03-16

### Fixed

- Some early code clean-up of the **sentinel** module. Added a HISTORY command to dump the
  current comprehensive list of job request status details to the logger in JSON format.

## 0.0.14-alpha - 2023-03-15

### Added

- First early working prototype of the **sentinel** module. This is an image analytical engine that 
  accepts job service requests over ZeroMQ. Parallelization is provided by a multi-processing design,
  allowing multiple tasks to run at once. Employs a dedicated I/O thread to supply image requests for 
  use in analysis tasks through a set of ring buffers in shared memory. 

## 0.0.13-alpha - 2022-11-16

### Fixed

- Corrected **camwatcher** filename generation for JPEG files when timestamp has no fractional second.
- Outpost state machine refinements. Begin adding missing logic to gaps in scene management functionality; 
  this addresses the runaway spyglass bug.

### Added

- Added an event delete command to **datapump**. This runs as a background task and will purge all 
  stored data for a specific event.

## 0.0.12-alpha - 2022-04-30

### Fixed

- Revisions and corrections to OAK camera neural net retrieval.

### Added

- Added `video_review_df.py` module, leaving original version in place for reference. This uses the 
  `DataFeed` for operation within a WSGI container, and represents the next logical step in the 
  evolution of this function.

## 0.0.11-alpha - 2022-04-20

### Added

- Added OAK-1 camera support for running DepthAI pipelines as the primary data collection device.

### Changed

- Support motion-only mode for event logging without object detection or tracking.
- Integrated object tracking support is now optional.

## 0.0.10-alpha - 2022-02-12

### Changed

- Now using **MessagePack** for marshalling IPC exchanges between `SpyGlass` and `Outpost`. 

### Fixed

- Replaced non-sensical approach to Outpost state management with something sane, and correct.

## 0.0.9-alpha - 2022-01-30

### Changed

- Code revised for operation within OpenVINO environment. Tested with an Intel NCS2 accelerator.
- Changed motion detector to use the OpenCV baseline MOG2 background subtraction library. 

### Added

- Added support for dlib correlation tracker.

## 0.0.8-alpha - 2022-01-10

### Fixed

- Additional work on the multiprocessing handshake. State management seems to have a loose
  tent stake. Somewhere. I'm beginning to think that the object detector is intermittently
  failing and returning bad data. Still looking for the real issue.

## 0.0.7-alpha - 2022-01-09

### Fixed

- Refinements to dance choreography between the Outpost and SpyGlass. 

## 0.0.6-alpha - 2022-01-08

### Added

- Added heartbeat logging from the **outpost**. This simply reports the current image 
  publishing frame rate at 5 minute intervals. This will be saved by the **camwatcher**
  whenever its internal logging level is set to INFO. It may be smarter to direct this 
  data down to the **imagehub** for access from the **librarian**.

### Changed

- *Over-publishing image data with 0MQ is not smart*. On a Raspberry Pi 4B, have measured 
  publishing rates for a (320,240) resolution image, compressed to JPEG, at 150+ frames
  per second. This is insane, at least for the hardware we're running on and any of the
  intended use cases driving this design. For a PiCamera, the hardware chip does not even 
  collect data faster than about 32 frames/second. Moving data is not free. There is always 
  a price to pay. Implemented an image publishing throttle for the `Outpost` based on configured 
  frame rate. Better to be kind to such a nice little box as the Raspberry Pi. High stress for 
  no payback? Always say no to such antics. 

## 0.0.5-alpha - 2022-01-05

### Changed

- Reduced latency between `Outpost` and the `SpyGlass` by moving 0MQ signaling protocol 
  from `tcp://127.0.0.1` to `ipc://name`. Had to swap the `ImageSender` and `ImageHub` 
  endpoints for this, which also provided for a more sensible handshake during initialization.  

## 0.0.4-alpha - 2022-01-05

### Changed

- Now using an imageZMQ REQ/REP pair to rig the IPC signaling between `Outpost` and the
  `SpyGlass`. The outpost implements a polling mechanism on the connection to provide 
  for a non-blocking receive until results are ready.

## 0.0.3-alpha - 2022-01-03

### Added

- First working prototype of the **datapump** module. This is a stand-alone process 
  intended for running on the same node as a **camwatcher**. This module services access requests 
  to the data and image sinks over imageZMQ transport, specifically for use with the `DataFeed`
  class from a process running on another node, such as the *Sentinel* itself.
- Added example **datafeed** module implementing `DataFeed` requests to the **datapump**. 
  This is still evolving. 
- Fleshed out initial `Outpost` functionality for the **imagenode** project, including an 
  early version of the `SpyGlass` as a multiprocessing vision analysis pipeline. 

### Changed

- Image folder path added as argument to CamData initialization.
- Adopted **simplejpeg** library in place of using **OpenCV** for more efficient frame file
  encoding/decoding.
- Corrected handling for updating the EventID used by an active **camwatcher** image subscriber.
- Data model for tracking events revised to substitute bounding rectangles for detected objects rather
  than an object centroid. Classification also added for those events where this can be
  estimated in real time.

## 0.0.2-alpha - 2021-02-20

### Added

- Added **camdata** module defining the new `CamData` class. Encapsulates access to CSV tracking
  data collected by the **camwatcher**. Provides `pandas.DataFrame` object references.
- Added systemd service definition for **camwatcher** operation.

### Changed

- Revert to baseline **imagehub** module. Camera handoff to the **camwatcher** is now performed
  directly from the **imagenode** outpost detector.
- Complete refactoring of all **imagenode** changes specific to **SentinelCam** outpost functionality
  into a single module.
- Image capture within the **camwatcher** now includes the frame capture time as a component
  of the filename. This more accurately associates timestamps with individual frames and improves
  performance of video replay. *Relying on filesystem timestamps for this was a misstep*.
- Utilization of PostgreSQL as a component of the **camwatcher** data layer replaced with 
  data tables mapped onto a set of CSV-format files; a simple and efficient capture method.
  Also provides the broadest integration support.
- Example event viewer application `video_review.py` revised to conform to the new **camwatcher** 
  data model. Functionality fleshed out to include date and event selection. Demonstrates use of 
  the `CamData` object to retrieve event and image data. 

### Deleted

- PyImageSearch folder removed from **imagenode**, and contents merged into a single **sentinelcam**
  library.

## 0.0.1-alpha - 2020-12-14

### Added

- First early working draft of **camwatcher** functionality.
- Includes a trivial viewer example for replaying a captured video event. 

### Changed

- Modified **imagenode** to implement log and image publishing. Sends a camera
  startup command to the connected **imagehub**. Added an experimental object 
  tracker to exercise **camwatcher** operations.
- Modified **imagehub** to implement the camera handoff to **camwatcher** from an 
  **imagenode** initialization.

[Return to main documentation page README](README.rst)
