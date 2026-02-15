# Facial Recognition and Machine Learning Pipeline

## Introduction

This is the first machine learning pipeline built for SentinelCam. It implements facial
recognition through an incremental cycle of data collection, model training, and deployment
— all running on embedded hardware. The design is meant to be foundational: a working pattern
for building and refining recognition models that can be extended to other domains like vehicle
identification.

The pipeline draws from data that SentinelCam collects naturally during normal operation. There
is no formal enrollment process. Individuals are learned through ongoing observation, with human
feedback guiding the model through a semi-supervised curation loop.

## Two task chains

The sentinel task system uses configurable chaining to build multi-step analysis pipelines.
Each task produces a typed tracking result set that becomes the input to the next task in
the chain. This mechanism created two parallel paths to facial recognition, driven by
differences in what the Outpost hardware can deliver.

### The original path

With a standard camera (PiCamera, USB webcam), the Outpost runs a SpyGlass in a parallel
process for real-time object detection. The SpyGlass only processes a subset of frames —
it takes one image at a time, runs inference, and waits for results before accepting the
next. The main pipeline keeps publishing at 30 FPS regardless. The SpyGlass produces the
`trk` tracking set: a record of what it found, but not a complete analysis of every frame.

At end of event, the Outpost submits `MobileNetSSD_allFrames` to the sentinel. This task
pulls the full image set from the data sink and runs MobileNet SSD detection against every
frame, producing the `obj` result set — a complete object detection record for the event.

The chain continues:

```
MobileNetSSD_allFrames (obj) → GetFaces (fd1) → FaceRecon (fr1)
```

`GetFaces` reads the `obj` results, filters for `person` detections, and runs face detection
on those frames. It does not search every frame — it uses the `obj` timestamps to skip directly
to frames where people were found. This produces the `fd1` tracking set: one record per detected
face with bounding rectangle coordinates.

`FaceRecon` takes the `fd1` results, extracts each face region, generates OpenFace embeddings,
and runs the SVM classifier. Results include identity, confidence probability, distance from
baseline, and a usability flag. This produces the `fr1` tracking set — the final recognition
output.

### The DepthAI path

An OAK camera with a DepthAI pipeline changes the equation. The on-board VPU runs MobileNet SSD
on every frame at 30 FPS, producing complete detection results in hardware. The `trk` result set
from this Outpost already contains full object detection data — there is no gap to fill, no need
for the `MobileNetSSD_allFrames` batch job.

The Outpost configuration for an OAK camera specifies `GetFaces2` as the sentinel task triggered
on person detection:

```yaml
sentinel_tasks:
    person: GetFaces2
```

`GetFaces2` is a task alias — it runs the same `GetFaces` code, but is configured to read from
`trk` instead of `obj`:

```yaml
# sentinel.yaml task_list
GetFaces2:
    alias: GetFaces
    config: /home/ops/sentinel/tasks/GetFaces.yaml
    chain: FaceRecon
    class: 1
```

The chain becomes:

```
GetFaces2 (fd1, from trk input) → FaceRecon (fr1)
```

Same face detection, same recognition — but skipping the batch object detection step entirely.
The Outpost hardware already did that work.

### Why this matters

The task aliasing and chaining mechanism means the same analysis code adapts to different hardware
configurations without modification. A standard PiCamera outpost and an OAK camera outpost both
produce identical `fd1` and `fr1` result sets; only the path to get there differs. This same
flexibility should be useful as new detection models and hardware accelerators are introduced.

The Outpost configuration also supports a `default` sentinel task for non-person detections.
An OAK camera watching a driveway might trigger `GetFaces2` for people and `VehicleSpeed` for
everything else — both configured in the same `sentinel_tasks` block.

## The recognition model

SentinelCam uses [OpenFace](https://cmusatyalab.github.io/openface/) embeddings as the
foundation for facial recognition. The OpenFace deep neural network transforms a face image
into a 128-dimension unit hypersphere representation. The Euclidean distance between two such
embeddings reflects how similar the faces are — smaller distance means more likely the same
person.

An SVM classifier is trained on embeddings generated from curated face images of known
individuals. At inference time, the `FaceRecon` task uses an ensemble approach:

- **SVM probability** — the classifier's confidence in its prediction
- **Euclidean distance** — comparison against per-person baseline thresholds
- **Fallback search** — when confidence is high but distance is large, search for the
  closest known face as confirmation

This combination addresses the open-set problem inherent to the system's design. SentinelCam
does not know in advance who will appear. It must distinguish between low confidence on a known
person and the first appearance of someone new. The distance metric helps make that distinction,
and the `usable` flag on each face capture marks whether it is a candidate for improving the model.

## Data collection and curation

Data collection is continuous and automatic. Every event with a person detection flows through
the task chain and produces `fr1` recognition results. The pipeline then feeds a curation cycle
that selects training data for the next model iteration.

### FaceSweep

The `FaceSweep` sentinel task scans recognition results for a given date, looking for face
captures worth including in the training set. It filters on several criteria:

- High-confidence recognitions (`proba > 0.99`) — confirmed examples of known individuals
- Unknown faces with large distance (`distance > 0.99`) — likely new people
- Borderline cases with small margin — faces near the decision boundary, valuable for
  refining the model

Each candidate is deduplicated by perceptual hash and added to `facelist.csv` — the master
registry of face images selected for model training. The facelist tracks the image coordinates,
face alignment landmarks, recognition statistics, and a status flag used to control which
selections are included in the next training run.

### Manual review

The facelist curation notebooks provide tools for reviewing FaceSweep candidates. Montages of
face crops are generated for visual inspection. A human reviewer confirms identities, flags
incorrect labels, and marks new individuals for enrollment. This review step is what makes
the pipeline semi-supervised — the system proposes candidates, a person validates them.

### Embedding generation

The `FaceDataUpdate` sentinel task reads the curated facelist, retrieves the original images
from the data sink, extracts and aligns each face, generates OpenFace embeddings, and writes
them to `facedata.hdf5`. This HDF5 file is the direct input to model training — a collection
of 128-dimensional embedding vectors keyed by face reference.

## Model training

Training runs on a dedicated node using Papermill to execute a parameterized Jupyter notebook.
The notebook loads the embeddings from `facedata.hdf5`, runs GridSearchCV to tune SVM
hyperparameters (kernel, regularization, gamma), trains the classifier on the full dataset,
and generates per-person baseline distance thresholds.

Training produces three artifacts:

| File | Purpose |
|------|---------|
| `facemodel.pickle` | Trained SVM classifier with label encoder |
| `baselines.hdf5` | Per-person distance thresholds (mean − std of training embeddings) |
| `facelist.csv` | The curated selections that produced this model |

Version naming follows a `YYYY-MM-DD` timestamp convention aligned with the model registry.
Each version represents a complete snapshot: the training data selections, the embeddings,
and the resulting model.

## Deployment

Trained models deploy through the model registry. The `facemodel.pickle` and `baselines.hdf5`
files go to sentinels. The `facelist.csv` goes to data sinks (where it is available to the
DataPump for downstream use). Services restart to load the new model, and the next person
detection begins producing `fr1` results against the updated classifier.

See [Model Registry](../devops/docs/deployment/MODEL_REGISTRY_IMPLEMENTATION.md) for version
management and deployment mechanics.

## The learning cycle

The pipeline forms a closed loop:

```
Outpost events → Sentinel task chain → fr1 recognition results
       ↑                                        ↓
  Deploy model                            FaceSweep curation
       ↑                                        ↓
  Train classifier ← FaceDataUpdate ← Manual review of facelist
```

Each iteration of the cycle improves the model. New individuals are enrolled. Existing subjects
gain more training examples from varied angles and lighting. The recognition thresholds tighten.
The DailyCleanup retention task adapts in response — events that were once kept for their
ambiguous recognition results can now be confidently discarded as the model improves.

## Challenges

The constraints of the embedded platform shape what is practical:

- **Small faces** — VGA and XGA resolution frames mean detected faces are often tiny, limiting
  the quality of embeddings
- **Lighting and angle** — outdoor cameras produce shadows, backlighting, profile views, and
  oblique angles that degrade recognition
- **Rolling shutter** — subjects in motion produce blur artifacts, particularly at high frame rates
- **Volume vs. quality** — a single event can yield hundreds of face detections, but only a handful
  may be suitable for training
- **Open-set recognition** — the system must detect new individuals, not just recognize known ones

Camera placement and lighting deserve careful attention. These physical factors often have more
impact on recognition quality than any software tuning.

## What is here, and what comes next

This is a working pipeline. It collects data, trains models, deploys them, and feeds the results
back into the next cycle. The sentinel task chain structure, the curation loop, and the model
registry integration are all operational.

The foundation is designed to extend. The same task chaining pattern that drives facial recognition
can be applied to vehicle identification, license plate reading, or any other domain where the
system collects data that can train a classifier. New tasks plug into the sentinel configuration,
new models register with the model registry, and new retention strategies slot into DailyCleanup.
