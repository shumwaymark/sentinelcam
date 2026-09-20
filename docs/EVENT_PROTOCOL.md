# Outpost Event Protocol

The wire contract between an **Outpost** and the **CamWatcher**. Three independent
transports carry it: a log publisher, a scene image publisher, and — on crop-publishing
(OAK) nodes — a high-resolution crop publisher. A fourth, the CamWatcher control port,
carries the optional dynamic registration handshake.

Port assignments are not fixed by this document. They are set per node by
`publish_log` / `publish_cam` (see [OUTPOST_CONFIGURATION.md](OUTPOST_CONFIGURATION.md))
and centrally in `devops/ansible/inventory/group_vars/all/sentinelcam_ports.yaml`.
The values shown below are the conventional defaults.

> **Verified against the shipping implementation.** Records are built in
> `imagenode/sentinelcam/eventmanager.py` and `eventsampler.py`; they are consumed in
> `camwatcher/camwatcher.py`. If this document and that code disagree, the code is right —
> fix this document.

---

## 1. Log stream (ZMQ PUB, :5565)

Every message is published under the topic `{nodename}.{LEVEL}` — for example
`east.INFO`. Two message families carry structured data, distinguished by a bare
string prefix on the message body:

| Prefix | Meaning |
|--------|---------|
| `ote`  | Object tracking event. The remainder of the string is a JSON object. |
| `fps`  | Publishing heartbeat, emitted on a periodic cadence. |

Everything else on this topic is ordinary log text.

### 1.1 `ote` records

Each record is emitted as the single string `"ote" + json.dumps(record)` — no delimiter
between the prefix and the JSON. A consumer strips the first three characters and parses
the rest.

Three fields are common to every record:

| Field | Type | Notes |
|-------|------|-------|
| `id`  | string | Event identifier, a UUID hex string. |
| `view` | string | The configured `viewname` of the camera. |
| `type` | string | Record discriminator: `start`, `trk`, `crp`, or `end`. |

The **node name is deliberately absent** from the record — it already arrives as the
root topic of the logger. The (node, view) pair is what lets a CamWatcher subscribed to a
multi-camera node tell the streams apart.

> **Field-name note.** The discriminator is `type` and the class label is `clas`. Earlier
> revisions of this protocol used `evt` and `class`; both were renamed and no longer
> appear on the wire. A consumer written against the old names silently matches nothing.

#### `start` — event opened

Sent once, when the tracker opens an event.

```json
{
  "id": "42fc4bb46cc611ebb942dca63261a32e",
  "view": "PiCam3",
  "type": "start",
  "new": true,
  "timestamp": "2026-09-20T07:32:12.856029",
  "camsize": [768, 432]
}
```

| Field | Notes |
|-------|-------|
| `new` | Whether this opens a *new* tracking set. The outpost always sends `true` for its own events. The CamWatcher's `SentinelAgent` reuses this same record shape for task results and sends `false` when appending to a tracking set that already exists. Combined with a file-existence check, it decides whether a new event-index row is written. |
| `camsize` | `[width, height]` of the scene frame, in pixels. Recorded in the event index and used downstream to size replay ring buffers. |

#### `trk` — tracking detail

Sent repeatedly while the event is open: once per analyzed frame per tracked object.

```json
{
  "id": "42fc4bb46cc611ebb942dca63261a32e",
  "view": "PiCam3",
  "type": "trk",
  "timestamp": "2026-09-20T07:32:12.856029",
  "obj": 17,
  "clas": "car",
  "rect": [412, 96, 585, 233]
}
```

| Field | Notes |
|-------|-------|
| `obj` | The tracker's persistent identity (`tid`) for this subject — an integer, stable across the whole event. This is the join key the re-identification work builds on. |
| `clas` | The **specific** label, and where available the confidence, as reported by the detector (`"car"`, `"person"`). The tracker itself tracks by base class; the specific label is reported here so downstream consumers keep the detail. |
| `rect` | `[x1, y1, x2, y2]` bounding box in **absolute scene pixels**. The tracker works in normalized coordinates and scales by `camsize` on emit. |

#### `crp` — crop correlation (OAK nodes only)

Sent when the `EventSampler` selects a high-resolution crop. This record is a
**correlation key only** — it carries no geometry.

```json
{
  "id": "42fc4bb46cc611ebb942dca63261a32e",
  "view": "PiCam3",
  "type": "crp",
  "timestamp": "2026-09-20T07:32:12.856029",
  "obj": 17,
  "seq": 4193,
  "det": 0,
  "clas": "car",
  "phase": "centre"
}
```

| Field | Notes |
|-------|-------|
| `seq` | Device frame sequence number. With `id` and `obj` it names the stored crop file. |
| `det` | Index of the detection within that frame. |
| `phase` | Which sampling phase selected this crop — `entry`, `centre`, `far` under the lateral-traversal strategy. |

The bounding box is **not duplicated here by design**. To reach a crop's geometry, join to
the `trk` record for the same `(id, obj)` at the same timestamp. The full chain is:

```
crop JPEG ──(id, obj, seq)──▶ crp record ──(id, obj, timestamp)──▶ trk geometry
```

#### `end` — event closed

```json
{
  "id": "42fc4bb46cc611ebb942dca63261a32e",
  "view": "PiCam3",
  "type": "end",
  "timestamp": "2026-09-20T07:32:41.204815",
  "tasks": [["MobileNetSSD_allFrames", 2], ["GetFaces", 1]]
}
```

`tasks` is a list of `[task_name, priority]` pairs for the CamWatcher to submit to the
**sentinel**, derived from the `sentinel_tasks` mapping and the set of object classes
actually seen during the event. Priority `1` is real-time post-event, `2` is default,
`3` is background.

The list is **gated by a minimum-viable-event threshold**: an event that ran for fewer
than the configured number of frames closes with `"tasks": []`. Image capture is *not*
gated this way — it is driven by `start`, so a short event still leaves frames behind.

### 1.2 `fps` heartbeat

Emitted periodically (every five minutes by default) as a plain formatted string, not
JSON:

```
fps(tick_count, looks, events, tick_rate, measured_fps)
```

A consumer parses the last comma-separated value for the measured publishing rate. The
CamWatcher tracks heartbeat arrival per outpost and flags a node whose heartbeat goes
stale.

The heartbeat's shape is a **parsing contract**. Anything that needs to announce itself —
a SpyGlass recycle, for instance — gets its own log line rather than a new heartbeat field.

> **Diagnostic worth knowing:** a heartbeat that keeps reporting a healthy frame rate while
> its `looks` counter stays frozen across consecutive reports means the main loop and the
> scene publisher are fine but detection has stopped. That signature is what the SpyGlass
> watchdog was built to catch.

---

## 2. Scene image stream (ImageZMQ PUB, :5567)

Every frame through the pipeline is published as a JPEG. ImageZMQ carries
`(text, image)` tuples, and the text field is an application-defined descriptor —
pipe-delimited here:

```
{nodename} {viewname}|{imagetype}|{ISO-8601 timestamp}
```

for example `east StreetView|jpg|2026-09-20T07:32:12.856029`.

The timestamp is the **image capture time**, taken from capture metadata rather than the
system clock at publish, so it matches the `timestamp` on the `trk` and `crp` records for
the same frame.

### Multiple cameras on one node

Publishing settings apply **once per imagenode**, not once per camera: a node binds a
single log publisher and a single image publisher regardless of how many cameras it runs.
Only the port numbers on the *first* camera entry in the YAML are used; values on
subsequent entries are ignored. Keep them identical anyway, so the configuration does not
mislead the next reader.

When several cameras publish from one node their frames **interleave** on the single
stream. Subscribers must filter by `viewname` — the CamWatcher always does.

---

## 3. Crop stream (ImageZMQ PUB, :5568 — OAK nodes only)

High-resolution, class-specific crops cut from the full-resolution sensor frame travel on
their own socket, so scene viewing and crop capture never contend. The descriptor carries
everything needed to name the file without consulting the log stream:

```
{viewname}|crop_{classname}|{event_id}|{tid}|{seqnum}|{phase}
```

The CamWatcher's `CropStreamWriter` parses these six fields directly and stores the image
as:

```
~/sentinelcam/crops/YYYY-MM-DD/{EventID}_{ObjID}_{Seqnum}_{Class}_{Phase}.jpg
```

That filename *is* the addressable key the **DataPump** dereferences for `get_crop_jpg`.
It is collision-free given one crop per `(objid, seqnum)`.

The crop stream is **not bracketed** by `start` / `end` — crops can arrive after the event
that owns them has closed. Consumers must not assume a crop's event is still open.

---

## 4. Control port (ZMQ REQ/REP, :5566) — `CamUp`

An outpost carrying the optional `camwatcher` connection setting introduces itself at
startup. This is a *dynamic, temporary* registration: a CamWatcher restart clears any ad
hoc registrations, and production nodes are expected to appear in the CamWatcher's own
configuration instead.

```json
{
  "cmd": "CamUp",
  "node": "outpost",
  "view": "PiCam3",
  "logger": "tcp://lab1:5565",
  "images": "tcp://lab1:5567"
}
```

The connection strings are assembled at startup from the configured `publish_log` and
`publish_cam` port numbers plus the node's actual hostname, read from the running network
configuration. On receipt the CamWatcher establishes its subscriptions and notes the new
node and view.

The control port also accepts `Agent` (ad hoc sentinel agents) and `DelEvt` (event
deletion, internal use only).

---

## See also

- [OUTPOST_CONFIGURATION.md](OUTPOST_CONFIGURATION.md) — the settings that drive all of this
- [TRACKING_ARCHITECTURE.md](TRACKING_ARCHITECTURE.md) — the lifecycle that produces the records
- [OUTPOST_HISTORY.md](OUTPOST_HISTORY.md) — how the protocol arrived at this shape
- [DATA_MANAGEMENT.md](DATA_MANAGEMENT.md) — how the stored result is retained and expired
