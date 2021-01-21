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
  - Clean up exception handling and internal logging code.
  - Create systemd service definition for production operation.
  - Refactor image capture and naming convention to more accurately associate timestamps
    with individual frames and improve performance of video replay. *Relying on filesystem
    timestamps for this was a misstep*.
  - Design data layer abstraction for tracking data, from a keep-it-simple perspective,
    to minimze overhead.

### To be deprecated

- Utilization of PostgreSQL as a component of the data layer to be dropped. Seems like 
  a poor fit for the analytical requirements of this application. 

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
