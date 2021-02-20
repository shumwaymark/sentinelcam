# Version History and Changelog

All notable changes to the **SentinelCam** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Ongoing development

- Begin building out the *sentinel* module. This will be the inference and modeling engine.
- Experiment with leveraging the new `send_threading` option in **imagenode** to supplement
  published video capture triggered from a motion event. By dumping the `cam_q` at the start 
  of a motion event, those frames could theoretically be used to assemble video from just prior 
  to that point in time.
- Refinements to object tracking code in **imagenode**. Begin initial experiments with 
  layering in some inference, such as object identifcation, here. Expecting to employ 
  a cascading technique to first inspect, then analyze a sampling of selected frames 
  over a sub-process call. 
- Continue development of the **camwatcher** module.
  - Move configuration into YAML file.
  - Clean up exception handling. 
- Experiment with adopting **simplejpeg** library for more efficient frame file encoding.
- Add missing node/view filtering functionality to video_review.py

### Known bugs

- For any video subscriber given a new lease on life, the event id used for the filenames
  should be updated to match the new index entry.

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
- Video capture within the **camwatcher** now includes the frame capture time as a component
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

- Modified **imagenode** to implement log and video publishing. Sends a camera
  startup command to the connected **imagehub**. Added an experimental object 
  tracker to exercise **camwatcher** operations.
- Modified **imagehub** to implement the camera handoff to **camwatcher** from an 
  **imagenode** intializtion.

[Return to main documentation page README](README.rst)
