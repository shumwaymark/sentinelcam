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
- Continue development and testing of the **sentinel** module. 
  - Provide inference results for storing with event data. The **datapump** module will
    need to become a two-way pump, for accepting data updates.
  - Support result signaling e.g. MQTT to Node-RED, and Twilio.
- Add missing support for **datapump** error codes in response messages using the first 
  element of the (text,data) tuple carried by *imageZMQ*.
- Continue monitoring the **camwatcher** module. Still have a few items on the TODO list.
  - Rather than terminating subprocess video writers at the end of each event, adapt these
    with a switch to turn subscriptions on and off as needed. Keep them loaded and ready 
    for faster startup on subsequent events. Design management controls to end after a 
    period of inactivity or gauge this based on work load. 
  - Move configuration into YAML file.
  - Confirm exception handling is correct.
- Continue development of video_review_df.py
  - Add missing node/view filtering functionality
  - Allow display of neural net results to be optional
  - Add functionality for real-time display of any camera view
- Experiment with leveraging the `send_threading` option in **imagenode** to supplement
  published image capture triggered from a motion event. By dumping the `cam_q` at the start 
  of a motion event, those frames could theoretically be used to assemble video from just prior 
  to that point in time. *Low priority*. Not sure the payoff is worth the effort and additional
  complexity.

### Known bugs

- `CamData` class fails with bad input values for date/event. Any `DataFeed` request can
  potentially query events that do not exist. Should probably return empty results for
  this condition.
- Just a general note of caution. Run this at your own risk. The `SpyGlass` task on the
  `Outpost` is still under active development, and highly experimental. 
- **imagenode** hangs when `SpyGlass` deployed and SIGTERM sent from `fix_comm_link()` by the
  `REP_watcher()`. This signal is not received by the child process. Need to devise a way to
  wire-in a facility to support this. 

## 0.0.15-alpha - 2023-03-16

### Fixed

- Some early code clean-up of the **sentinel** module. Added a HISTORY command to support dumping
  complete task history to the logger in JSON format.


## 0.0.14-alpha - 2023-03-15

### Added

- First early working prototype of the **sentinel** module. The `Sentinel` accepts job service
  request over ZMQ. Parallelization is provided by a multi-processing design, allowing multiple 
  task requests to run at once. Employs a dedicated I/O thread to supply image requests to analysis
  tasks through a set of ring buffers in shared memory. 

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

- Added video_review_df.py module, leaving original version in place for reference. This uses the 
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

- *Over-publishing image data with ZMQ is not smart*. On a Raspberry Pi 4B, have measured 
  publishing rates for a (320,240) resolution image, compressed to JPEG, at 150+ frames
  per second. This is insane, at least for the hardware we're running on and any of the
  intended use cases driving this design. For a PiCamera, the hardware chip does not even 
  collect data faster than about 32 frames/second. Moving data is not free. There is always 
  a price to pay. Implemented an image publishing throttle for the `Outpost` based on configured 
  frame rate. Better to be kind to such a nice little box as the Raspberry Pi. High stress for 
  no payback? Always say no to such antics. 

## 0.0.5-alpha - 2022-01-05

### Changed

- Reduced latency between `Outpost` and the `SpyGlass` by moving ZMQ signaling protocol 
  from `tcp://127.0.0.1` to `ipc://name`. Had to swap the `ImageSender` and `ImageHub` 
  endpoints for this, which also provided for a more sensible handshake during initialization.  

## 0.0.4-alpha - 2022-01-05

### Changed

- Now using an *imageZMQ* REQ/REP pair to rig the IPC signaling between `Outpost` and the
  `SpyGlass`. The outpost implements a polling mechanism on the connection to provide 
  for a non-blocking receive until results are ready.

## 0.0.3-alpha - 2022-01-03

### Added

- First working prototype of the **datapump** module. This is a stand-alone process 
  intended for running on the same node as a **camwatcher**. This module services access requests 
  to the data and image sinks over *imageZMQ* transport, specifically for use with the `DataFeed`
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
