# Version History and Changelog

All notable changes to the **SentinelCam** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Ongoing development

- Continue refinements to `Outpost` implementation. 
  - Streamline the marshalling of results from `SpyGlass` back to `Outpost`, probably
    with **MessagePack**. 
  - Provide for motion-only option for capture/logging sans object detection and tracking.
  - Modernize object detector capabilites with support for newer algorithms.
  - Implement filtering mechanism based on object detection results.
  - Begin designing support for a model deployment framework that can be used
    to support custom lenses as another layer beneath object detection.
- Continue design of the *Sentinel* module. This will become the inference and modeling engine.
  Much of the tooling for `SpyGlass` lays the foundation for how jobs will be managed.
- Continue monitoring the **camwatcher** module. Still have a few items on the TODO list.
  - Rather than terminating subprocess video writers at the end of each event, adapt these
    with a switch to turn subscriptions on and off as needed. Keep them loaded and ready 
    for faster startup on subsequent events. Design management controls to end after a 
    period of inactivity or gauge this based on work load. 
  - Move configuration into YAML file.
  - Confirm exception handling is correct.
- Continue development of video_review.py
  - Add missing node/view filtering functionality
  - Adapt to use the `DataFeed` for operation from an application server
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

## 0.0.8-alpha - 2022-01-10

### Fixed

- Additional work on the multiprocessing handshake. State management seems to have a loose
  tent stake. Somewhere. I'm beginning to think that the object detector is intermitently
  failing and returning bad data. Still looking for the real issue.

## 0.0.7-alpha - 2022-01-09

### Fixed

- Refinements to dance choreography between the Outpost and SpyGlass. 

## 0.0.6-alpha - 2022-01-08

### Added

- Added heartbeat logging from the **outpost**. This simply reports the current image 
  publishing frame rate once per minute. This will be saved by the **camwatcher**
  whenever its internal logging level is set to INFO. It may be smarter to direct this 
  data down to the **imagehub** for access from the **librarian**.

### Changed

- *Over-publishing image data with ZMQ is not smart*. On a Raspberry Pi 4B, have measured 
  publishing rates for a (320,240) resolution image, compressed to JPEG, at 150+ frames
  per second. This is insane. In no universe does that make sense. For a PiCamera, the 
  hardware chip does not even collect data faster than about 32 frames/second. Moving 
  data is not free. There is always a price to pay. Implemented an image publishing 
  throttle for the `Outpost` based on configured frame rate. Better to be kind to such 
  a nice little box as the Raspberry Pi. High stress for no payback? Always say no to 
  such antics. 

## 0.0.5-alpha - 2022-01-05

### Changed

- Achieved considerably lower latency between `Outpost` and the `SpyGlass` by moving 
  ZMQ signaling protocol from `tcp://127.0.0.1` to `ipc://name`. Had to swap the
  `ImageSender` and `ImageHub` endpoints for this, which also provided for a more 
  sensible handshake during initialization.  

## 0.0.4-alpha - 2022-01-05

### Changed

- Now using a ZeroMQ REQ/REP pair to rig the IPC signaling mechanism between `Outpost` and 
  the `SpyGlass`. The outpost implements a polling mechanism on the connection to provide 
  for a non-blocking receieve until results are ready.

## 0.0.3-alpha - 2022-01-03

### Added

- First working prototype of the **datapump** module. This is a stand-alone process 
  intended for running on the same node as a **camwatcher**. This module services access requests 
  to the data and image sinks over *imageZMQ* transport, specifically for use with the `DataFeed`
  class from a process running on another node, such as the *sentinel* itself.
- Added example **datafeed** module implementing `DataFeed` requests to the **datapump**. 
  This is still evolving. 
- Fleshed out intitial `Outpost` functionality for the **imagenode** project, including an 
  early version of the `SpyGlass` as a multiprocessing vision analysis pipeline. 

### Changed

- Image folder path added as argument to CamData initialization.
- Adopted **simplejpeg** library in place of using **OpenCV** for more efficient frame file
  encoding/decoding.
- Corrected handling for updating the EventID used by an active **camwatcher** image subscriber.
- Data model for tracking events revised to substitue bounding rectangles for detected obects rather
  than an object centroid. Classname also added for those events where this can be
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
- Example event wiewer application `video_review.py` revised to conform to the new **camwatcher** 
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
  **imagenode** intializtion.

[Return to main documentation page README](README.rst)
