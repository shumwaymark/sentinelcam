# Version History and Changelog

All notable changes to the **SentinelCam** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Ongoing development

- Continue refinements to `Outpost` implementation. 
  - Modernize object detector capabilities with support for newer algorithms.
  - Implement filtering mechanism based on object detection results.
  - Begin designing support for a model deployment framework that can be used
    to support custom lenses as another layer beneath object detection.
- Continue development and testing of the **sentinel** module. Support result signaling 
  to Twilio, and to Node-RED via MQTT.
- Implement internal housekeeping on the **sentinel** to periodically dump oldest job history.
  Needed to avoid an obvious memory leak. Perform as a routine task during quiet periods.
- Support a job runtime limit as a configurable setting per task engine. Provide tolerance
  based on the queue length for tasks waiting in that job class.  
- Add missing support for **datapump** error codes in response messages using the first 
  element of the (text,data) tuple carried by imageZMQ.
- Need maintenance shell script to clear out empty **camwatcher** data folders after last
  event has been purged for a date.
- Add missing health-check monitor from the **camwatcher** to detect and restart a stalled
  **imagenode**. 

### Known bugs

- Just a general note of caution. Run this at your own risk. All major components are under 
  active development. SentinelCam is an on-going research experiment which may, at times, 
  be somewhat unstable around the edges.

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
