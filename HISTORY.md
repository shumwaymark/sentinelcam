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
  - Implement result signaling to Twilio, to Node-RED via MQTT, and to **imagehub** logging 
    to help support reponsiveness to events in progress as well as for knowledge and state 
    management throughout the larger system. 
  - Provide an abstraction to support a reusable design pattern for ring buffer control 
    based on an object detection result filter.
  - Implement internal housekeeping to periodically purge oldest job history. This is 
    needed to avoid an obvious memory leak. Perform as a routine task during quiet periods.
    Incorporate a job history report of statistics and performance metrics as an output. This
    could ultimately fuel a **sentinel** health check, perhaps built to support agency based on 
    self-diagnosis. 
  - Support a job runtime limit as a configurable setting per task engine? Provide tolerance
    based on the queue length for tasks waiting in that job class.  
- **datapump** needs data sink storage and data analysis with clean-up and reporting as a nightly 
  task. Will need control panel instrumentation for this as well, including perhaps charts
  of the storage breakdown, utilization, and available capacity of the data sinks. 
- Add missing health-check monitor from the **camwatcher** to detect and restart a stalled
  **imagenode**. 

### Known bugs

- Just a general note of caution. Run this at your own risk. All major components are under 
  active development. SentinelCam is an on-going research experiment which may, at times, 
  be somewhat unstable around the edges.

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

- Move DepthAI model file name into application configuration, and incorporate into the model
  registry and devops playbooks.

## 0.2.1-alpha - 2025-12-30

### Added

- Full support for Google Coral USB Accelerator, for both the outposts and sentinels. This includes 
  software package provisioning, application configuration, as well as model registry and deployment 
  for `mobilenet_ssd_edgetpu` and `face_detection_edgetpu`. Support for face detection on the outposts, 
  as a supplementary lens for the `SpyGlass`, is an outstanding To Do List item.

### Changed

- Code and model deployment tasks now pull from the repositories on the primary data sink directly to each 
  target node via locally executed rsync commands. An operational SSH key is stored as a variable in
  an Ansible encrypted vault for deployment and use by target nodes. See `playbooks/deploy-ssh-key.yaml` 
  for details.

## 0.2.0-alpha - 2025-12-09

### Added

- *Complete DevOps infrastructure*: Full Ansible-based CI/CD pipeline and configuration management
  system. A comprehensive automation framework for deployment, configuration, and maintenance of all 
  system components:
  
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
