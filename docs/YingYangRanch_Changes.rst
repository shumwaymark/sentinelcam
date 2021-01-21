==========================================
Changes to imagenode and imagehub projects
==========================================

.. contents::

Overview
========

The **SentinelCam** project has relied heavily on the **imagenode** and **imagehub** projects
as an initial foundation to build upon. 

These projects offered already working, readily exploitable, solutions for camera image capture
and video analysis. The hub and spoke architecture dovetails nicely with the **SentinelCam** design,
and include many additional features for handling sensors and vision tasks not requiring video capture. 

Initial modifications to these two projects consist of adding support for log and video publishing over
ZMQ as configurable options. All the new configuration items, and what they do, are documented below.

For complete details on the use of these modules, refer to the existing baseline documentation.

- `Baseline imagenode documentaiton <https://github.com/shumwaymark/imagenode/README.rst>`_
- `Baseline imagehub documentation <https://github.com/shumwaymark/imagehub/README.rst>`_

==========================
Modifications to imagenode
==========================

Modifications to **imagenode** have focused on breathing life into the **SentinelCam** *outpost* node.
There are two aspects to this effort. The first is to provide support for log and video publishing.
Additionally, a simple motion tracker is included to prove out the design of the **camwatcher**.

Example configuration with new options
======================================

An example imagenode.yaml file is provided below to demonstrate how all of the new configuration
options are specified. These include the ``publish_cams`` and ``publish_log`` options of the ``node``
setup section, and the ``video`` option that is applied on a per-camera basis. There is also a
new ``tracker`` detector which has been included for testing the design.

.. code-block:: yaml

  # Settings file imagenode.yaml -- sentinelcam test #1
  ---
  node:
    name: outpost
    publish_log: 5565 # activates log publishing to camwatcher 
    publish_cams: 5567 # activates camera option for video frame publishing 
    queuemax: 50
    patience: 10
    heartbeat: 10
    send_type: jpg
    print_settings: True
  hub_address:
    H1: tcp://data1:5555
  cameras:
    P1:
      viewname: PiCamera
      resolution: (640, 480)
      framerate: 32
      vflip: True
      video: True # all video frames published
      detectors:
        tracker:
          ROI: (25,50),(60,85)
          draw_roi: ((255,0,0),5)

New node settings
=================

Two optional ``node`` settings have been added. These are the ``publish_cams`` and ``publish_log``
options described in further detail below. These two settings are intended to work together as part
of the complete solution. Ultimately, it should be possible to use these independently of each other. 

When log publishing has been configured, **imagenode** initialization constructs a camera startup
message and sends it over the log to be delivered to a **camwatcher**. This startup message informs 
the **camwatcher** of camera video publishing settings.

.. code-block:: yaml

  publish_log: port number to use for log publishing
  publish_cams: port nunber to use for video frame publishing 

publish_log
-----------

The optional ``publish_log`` setting takes a single argument: a numeric port number. This activates 
logfile publishing over ZMQ, binding to the specified port. Once activated, all calls to the logger use
this mechanism. The root topic for the logger will be set to the configured node name. This helps the
**camwatcher** easily filter messages based on the source of the data.

When this configuration option has been specified, a specific camera startup message is constructed and
sent to the connected **imagehub** during initialization. This happens immediately after logfile publishing
has been activtated. The format of this startup message is in 3 parts, using the "|" character as a field
delimiter.

.. code-block::

  node name|$CameraUp|camera handoff

These three fields are defined as follows:

- ``node name`` - The node name from the configuration file. 
- ``$CameraUp`` - The literal text as shown. Used to indicate that an *outpost* initilization is in
  progress. The leading dollar sign is a signal to the **imagehub** that this message is a command.
- ``camera handoff`` - A python dictionary structure in JSON format containing publishing parameters
  to be passed to a **camwatcher** process. A basic set of values related to the node itself and a 
  list of camera view details for each camera tagged with the ``video: True`` setting. The following
  camera handoff structure reflects the example YAML configuration file presented earlier. The ``host``
  field is the actual hostname of the node needed for network addressing.
  
  .. code-block:: json

    {
      "node": "outpost",
      "host": "lab1",
      "log": 5565,
      "video": 5567,
      "cams": [
        { "PiCamera": [640, 480] }
      ]
    }

After this startup message is sent to the **imagehub**, the camera handoff structure is relayed to 
its configured **camwatcher**. If this exchange is successful, an "OK" response is returned to the
**imagenode**, and the processing pipeline is started. Otherwise, **imagenode** initialization fails.  

publish_cams
------------

The optional ``publish_cams`` setting also has a numeric port number argument. This activates
video publishing over ImageZMQ, binding to the specified port. For each camera configured with
the ``video: True`` setting, all frames are published. Given the hostname and port, any client 
can subscribe to a live camera feed on-demand. 

Each frame is published as a JPEG-compressed image. The publishing frame rate depends on the
length of the vision processing pipeline of the **imagenode**. Multiple cameras, large image
sizes, and processing complexity, can each have compounding adverse effects on the overall
velocity.

New cameras settings
====================

Two new items can be applied to each entry within the ``cameras`` block of the YAML configuration
file. The ``video`` option works in concert with the ``publish_cams`` option for the ``node``, and
indicates that every image frame from this camera is to be published. The ``tracker`` block activates
the motion tracker used to stream motion events to the connected **camwatcher**.

video
-----

The ``video`` setting is a True/False value needed to select any camera for publishing whenever
the ``publish_cams`` option described above has been specified.

.. code-block:: yaml

  video: A True or False value indicating whether video publising is active for this camera

tracker
-------

This simple motion tracker works as one of the ``detectors`` for a camera. It does not yet
provide for any specialized tunable parameters of its own. Current otions consist of only the 
standard ``ROI`` and ``draw_roi`` parameters already well documented in the baseline module.

Constructed as a proof of concept for the *outpost* / **camwatcher** design, the tracker employs
a backgroud subtraction model to detect changes between individual frames. The bounding rectangles
for each assumed object are identified, associated with an identification number, and movement
between subsequent frames tracked based on the geometric centroid of each object.

There are three motion events reported by the ``tracker``. There is a single reported item for the
start of each event, and another for the end of the event when no nore motion is occuring. The third
reported item is the tracking data itself, which is reported multiple times during the lifespan of
the event, for each frame and tracked object. All of the data being reported for these three
conditions are stored within a python dictionary structure, and published over the logger in JSON
format.

Each tracking message has three fields in common. There is an ``id`` field which serves as the
event identifier. The ``view`` field contains the configured ``viewname`` for the ``camera``. Note
that the ``node`` name is not included in these messages since it is already being passed as the
root topic of the logger. This allows the **camwatcher** o differentiate between messages when
subscribing to multiple *outpost* nodes simultaneously. The third common field is the ``evt``
field, which can contain one of three values as described below. 

1) Event start, the ``evt`` field contains the text ``start``. This message is sent once, when
   motion is first detected. The ``fps`` field reflects the velocity of the pipeline at the
   start of the event in frames per second. This value is calculated based on a rolling average
   looking back over the previous 160 frames. Assuming a rate of 32 frames/second, this would 
   represent the average pipelne velocity for the 5 seconds prior to the start of the event.  

   .. code-block:: json

     {
       "id": 999,
       "view": "PiCamera",
       "evt": "start",
       "fps": 41
     }

2) Object tracking data, the ``evt`` field contains the text ``trk``. This message is sent multiple
   times while the event is in progress, for each analyzed frame and tracked object within the frame.
   The ``obj`` field contains an object identifier. The ``cent`` field is the x,y coordinates of the
   geometric centroid of the object being reported. 

   .. code-block:: json

     {
       "id": 999,
       "view": "PiCamera",
       "evt": "trk",
       "obj: 999,
       "cent": [456, 123]
     }

3) End of the event, the ``evt`` field contains the text ``end``. Sent when no more motion is 
   detected. Any other fields contained in the structure beyond what is portrayed in the example
   below should be ignored. There could be extraneous data carried in this message left over from
   the prior tracking event. 

   .. code-block:: json

     {
       "id": 999,
       "view": "PiCamera",
       "evt": "end"
     }

=========================
Modifications to imagehub
=========================

The **imagehub** module has been adpated to support the camera handoff protocol to a
connected **camwatcher** as described above. 

The first draft of this design may appear to leave the **imagehub** without much of a role.
However, current planning conceptually provides for full integration and compatibility with
Jeff's *Librarian*. Any *outpost* node should be able to provide not only video and log
publishing functionality, but also host any other sensors which conform to the Ying Yang
Ranch design pattern.

- Using the **imagehub** as an intermediary between an **imagenode** and **camwatcher** is
  to be deprecated in an upcoming **SentinelCam** release. 

Example configuration with camwatcher
=====================================

A new section has been added to the configuration file to identfy the connected **camwatcher**.
An example imagehub.yaml file is provided below to demonstrate how this is specified in context.

.. code-block:: yaml

  # Settings file imagehub.yaml -- sentinelcam test #1
  ---
  hub:
    queuemax: 500 # maximum size of queue of images to write
    patience: 1  # how often to log a lack of message in minutes
    print_settings: False
    data_directory: /mnt/usb1/imagedata
    max_images_write: 1500  # a cap on images to write in one day
  camwatcher:
    CW1: tcp://localhost:5566

camwatcher settings
===================

Rather than including the address of the **camwatcher** with the existing configuration
items for the ``hub``, a new ``camwatcher`` section has been added. The intent here was
to allow support for a list of available nodes for resiliency and distributing the
workload. For this initial implementation, only a single **camwatcher** is supported.

CW1
---

The ``CW1`` item specifies the connection string to the control port of the **camwatcher**. 

`Return to main documentation page README <../README.rst>`_
