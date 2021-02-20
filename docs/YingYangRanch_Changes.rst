==========================================
Changes to imagenode and imagehub projects
==========================================

.. contents::

Overview
========

The **SentinelCam** project has relied heavily on the **imagenode** and **imagehub** projects
as an initial foundation to build upon. 

These projects offered already working, readily exploitable, solutions for camera image capture
and analysis. The hub and spoke architecture dovetails nicely with the **SentinelCam** design.
The **imagenode** module includes many additional features for handling other types of sensors,
and vision tasks not requiring video capture. 

Only the **imagenode** module has been modified to support this project. Significant changes consist of 
adding support for log and video publishing over ZMQ as configurable options. The **imagehub** continues
to fulfill its existing role without change. All new configuration items, and what they do, are documented
below.

For complete details on the use of these modules, refer to the existing baseline documentation.

- `Baseline imagenode documentaiton <https://github.com/shumwaymark/imagenode/blob/master/README.rst>`_
- `Baseline imagehub documentation <https://github.com/shumwaymark/imagehub/blob/master/README.rst>`_

==========================
Modifications to imagenode
==========================

Modifications to **imagenode** have focused on breathing life into the **SentinelCam** *outpost* node.
There are two aspects to this effort. Providing support for log and video publishing, and a simple motion
tracker to fuel the **camwatcher** engine.

Example configuration with new options
======================================

An example imagenode.yaml file is provided below to demonstrate how the new configuration
options are specified. These items have all been incorporated into the ``outpost`` detector 
built for testing and proving the design. These include the ``publish_cams`` and ``publish_log`` 
options along with the ``camwatcher`` specification for the connection string to the **camwatcher** 
control port. 

.. code-block:: yaml

  # Settings file imagenode.yaml -- sentinelcam test #2
  ---
  node:
    name: outpost
    heartbeat: 10
    patience: 5
    REP_watcher: True
    queuemax: 50
    send_type: jpg
    send_threading: True
    print_settings: False
  hub_address:
    H1: tcp://data1:5555
  cameras:
    P1:
      viewname: PiCamera
      resolution: (640, 480)
      framerate: 32
      vflip: False
      detectors:
        outpost:
          publish_cam: 5567 # activates video frame publishing
          publish_log: 5565 # activates logfile publishing over ZMQ
          camwatcher: tcp://data1:5566 # connect to camwatcher control port
          ROI: (10,20),(70,80)
          draw_roi: ((255,0,0),1)
          draw_time: ((255,0,0),1)  
          draw_time_org: (5,5)  
          draw_time_fontScale: 1 

Settings for the outpost detector
=================================

There are three basic settings related to communication with the **camwatcher**:
``publish_cams``, ``publish_log``, and `camwatcher`. Each option is descrinbed in 
further detail below.  

.. code-block:: yaml

  publish_cam: port nunber to use for video frame publishing 
  publish_log: port number to use for log publishing
  camwatcher: connection string to the camwatcher control port

publish_cam
------------

The optional ``publish_cam`` setting takes a single argument: a numeric port number. This 
activates video publishing as an ``imagezmq.ImageSender``, binding to the specified port. 
Each image passing through the pipeline for the camera is published. This allows any client
to subscribe as an ``imagezmq.ImageHub`` for access to a live camera feed as needed.

Each frame is published as a JPEG-compressed image. The publishing frame rate depends on the
length of the vision processing pipeline of the **imagenode**. Multiple cameras, large image
sizes, additional detectors, and processing complexity, can each have compounding adverse 
effects on the velocity out to the client endpoint.

publish_log
-----------

The optional ``publish_log`` setting also has a numeric port number argument. This activates 
logfile publishing over **PyZMQ**, binding to the specified port. Once activated, all calls to the 
logger use this mechanism. The root topic for the logger will be set to the configured node name. 
This helps any interested subscriber easily filter messages based on the source of the data.

camwatcher
----------

This configuration option introduces the **imagenode** to the **camwatcher**. The ``publish_log`` option
must also be specifed, or this setting will be ignored. For intended use as designed, ``publish_cam`` 
should also be included. 

During startup, a camera handoff message is constructed and sent to the **camwatcher** during initialization.
This happens immediately after logfile publishing has been activtated. This startup message provides the 
**camwatcher** with a description of the camera, and information for establishing subscriptions to **imagenode** 
publishing services. The format of this startup message is in 2 parts, using the "|" character as a field delimiter.

.. code-block::

  CameraUp|camera_handoff_msg

These three fields are defined as follows:

- ``CameraUp`` - The literal text as shown. Used to indicate that an *outpost* initialization is in
  progress. 
- ``camera_handoff_msg`` - A python dictionary structure in JSON format containing publishing parameters
  to be passed to a **camwatcher** process. A basic set of values related to the node and camera view. 
  The following camera handoff structure reflects the example YAML configuration file presented earlier.
  The ``host`` field is the actual hostname of the node needed for network addressing.
  
  .. code-block:: json

    {
      "node": "outpost",
      "host": "lab1",
      "log": 5565,
      "video": 5567,
      "view": "PiCamera" 
    }

If this message exchange is successful, an "OK" response is returned to the **imagenode** and
initialization continues. Otherwise, **imagenode** initialization fails.  

Description of the outpost detector
===================================

The publishing settings described above are only allocated once per *outpost* node. This insures 
that any given *outpost* will have only a single logging publisher, and single video publisher. It
may be desirable to define more that one ``outpost`` detector per camera view, each with a different 
region of interest. The publising settings described above only need to be supplied once, duplicate
entries for these will be ignored.

outpost
-------

This simple motion tracker works as one of the ``detectors`` for a camera. It does not yet
provide for any specialized tunable parameters of its own. Current otions consist of only the 
standard ``ROI`` and ``draw*`` parameters already well described in the baseline documentaiton.

Constructed as a proof of concept for the *outpost* / **camwatcher** design, the tracker employs
a backgroud subtraction model to detect changes between individual frames. The bounding rectangles
for each assumed object are identified, associated with an identification number, and movement
between subsequent frames tracked based on the geometric centroid of each object.

There are three motion events reported by the ``outpost``. There is a single reported item for the
start of each event, and another for the end of the event when no nore motion is occuring. The third
reporting point is the tracking data itself, which is published repetitively across multiple frames 
throughout the lifespan of the event, for each frame and tracked object. All of the data being reported 
for these three conditions is stored within a python dictionary structure, and published over the logger 
in JSON format.

Each tracking message is associated with a specific event and camera view. The ``id`` field serves as the 
event identifier, this is a UUID value for uniqueness. The ``view`` field contains the configured ``viewname`` 
for the ``camera``. Note that the ``node`` name is not included in these messages since it is already being 
passed as the root topic of the logger. This pairing of node and view allows the **camwatcher** to differentiate 
between messages when subscribing to multiple *outpost* nodes simultaneously.

The third common field is the ``evt`` field, which can contain one of three values as described below. 

For efficiency, a timestamp is not currently included in these messages. Timestamps must be added by the
receiving system. Admittedly, this is less than accurate. However, as long as the end-to-end pipeline is 
opertaing efficiently, there should be at most just a few milliseconds of difference between the actual 
time of the observation, and the logged/reported time. 

1) Event start, the ``evt`` field contains the text ``start``. This message is sent once, when
   motion is first detected. The ``fps`` field reflects the velocity of the pipeline at the
   start of the event in frames per second. This value is calculated based on a rolling average
   looking back over the previous 160 frames. A reported rate of 32 frames/second would 
   reflect the average pipelne velocity for the 5 seconds prior to the start of the event.  

   .. code-block:: json

     {
       "view": "PiCamera",
       "id": "42fc4bb46cc611ebb942dca63261a32e",
       "evt": "start",
       "fps": 34
     }

2) Object tracking data, the ``evt`` field contains the text ``trk``. This message is sent multiple
   times while the event is in progress, for each analyzed frame and tracked object within the frame.
   The ``obj`` field contains an object identifier. The ``cent`` field is the x,y coordinates of the
   geometric centroid of the object being reported. 

   .. code-block:: json

     {
       "view": "PiCamera",
       "id": "42fc4bb46cc611ebb942dca63261a32e",
       "evt": "trk",
       "obj": 999999,
       "cent": [0000, 9999]
     }

3) End of the event, the ``evt`` field contains the text ``end``. Sent when no more motion is 
   detected. Any other fields contained in the structure beyond what is portrayed in the example
   below should be ignored. There could be extraneous data carried in this message left over from
   the prior tracking event. 

   .. code-block:: json

     {
       "view": "PiCamera",
       "id": "42fc4bb46cc611ebb942dca63261a32e",
       "evt": "end"
     }

=====================
Notes on imagehub use
=====================

There are no modifications needed to the **imagehub** module. All planning and design goals provide 
for full support and compatibility with Jeff's *Librarian*. Any *outpost* node should be able to 
provide not only video and log publishing functionality, but also host any other sensors which conform 
to the Ying Yang Ranch design pattern.

`Return to main documentation page README <../README.rst>`_
