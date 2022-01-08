============================
Changes to imagenode project
============================

.. contents::

Overview
========

The **SentinelCam** project has relied heavily on the **imagenode** and **imagehub** projects
as an initial foundation to build upon. 

These projects offered already working, readily exploitable, solutions for camera image capture
and analysis. The hub and spoke architecture dovetails nicely with the **SentinelCam** design.
The **imagenode** module includes many additional features for handling other types of sensors
and vision tasks not requiring video capture. 

Only the **imagenode** module has been modified for this project. Significant changes consist of adding
support for log and video publishing over ZMQ as configurable options. The ``Outpost`` detector is
a multiprocessing solution using shared memory for passing image data. Details regarding implementation
and operation are documented below.

The **imagehub** continues to fulfill its existing role without change.

For complete details on the use of these two modules, please refer to the baseline documentation.

- `Baseline imagenode documentaiton <https://github.com/shumwaymark/imagenode/blob/master/README.rst>`_
- `Baseline imagehub documentation <https://github.com/shumwaymark/imagehub/blob/master/README.rst>`_

==========================
Modifications to imagenode
==========================

Modifications to **imagenode** are fully encapsulated by the the ``outpost`` detector. There are
two aspects to this effort. Providing support for log and video publishing, and developing an object
tracker to fuel the **camwatcher** engine.

Example configuration with new options
======================================

An expanded example imagenode.yaml file is provided below to demonstrate how the new configuration
options are specified. All **SentinelCam** configuration items are specified as settings for the 
``outpost`` detector.

.. code-block:: yaml

  # Settings file imagenode.yaml -- sentinelcam test #3
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
          publish_cam: 5567            # activates video frame publishing
          publish_log: 5565            # activates logfile publishing over ZMQ
          camwatcher: tcp://data1:5566 # connect to camwatcher control port
          spyglass: (640, 480)         # important, must match camera "resolution" above
          tracker: kcf                 # [csrt, kcf, boosting, mil, tld, medianflow, mosse]
          skip_frames: 30
          detectobjects: mobilenetssd  # [mobilenetssd, yolov3]
          mobilenetssd:
            prototxt_path: /home/pi/imagenode/outpost/mobilenet_ssd/MobileNetSSD_deploy.prototxt
            model_path: /home/pi/imagenode/outpost/mobilenet_ssd/MobileNetSSD_deploy.caffemodel
            confidence: 0.5
            target: cpu                # [cpu, myriad]          
          yolov3:  
            yolo_path: /home/pi/imagenode/outpost/yolo-coco
            confidence: 0.5
            threshold: 0.3
            consider: [person, car, truck, dog, cat, bird, bicycle, motorbike] 
          ROI: (10,20),(70,80)
          draw_roi: ((255,0,0),1)
          draw_time: ((255,0,0),1)  
          draw_time_org: (5,5)  
          draw_time_fontScale: 1 

Camwatcher connection settings
==============================

There are three basic configuration settings related to communication with the **camwatcher**.

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

These two fields are defined as follows:

- ``CameraUp`` - The literal text as shown. Used to indicate that an *outpost* initialization is in
  progress. 
- ``camera_handoff_msg`` - A dictionary structure in JSON format containing publishing parameters
  to be passed to a **camwatcher** process. A basic set of values related to the **imagenode** itself. 
  The following camera handoff structure reflects the example YAML configuration file presented earlier.
  The ``host`` field is the actual hostname of the node needed for network addressing.
  
  .. code-block:: json

    {
      "node": "outpost",
      "host": "lab1",
      "log": 5565,
      "video": 5567
    }

If this message exchange is successful, an "OK" response is returned to the **imagenode** and
initialization continues. Otherwise, **imagenode** initialization fails.  

--------------------------------
Publishing with multiple cameras
--------------------------------

The publishing settings described above are only applied once per **imagenode**. This insures 
that any given node will have only a single logging publisher, and single video publisher. It
may be desirable to have multiple cameras on a single node, each with a different perspective.
The publising settings described above only need to be supplied once, duplicate entries for
these will be ignored.

Be aware that when simultaneously publishing from multiple cameras on a single node, image frames 
from each camera will be interleaved in the stream. The **camwatcher** is aware of this, and 
always filters by ``viewname`` when subscribing to a video stream. 

This is possible because the **imageZMQ** library is designed to send and receive payloads that 
are (text, image) tuples where the first element is a string with an application specific value.
The **imagenode** uses this text field for a ``"nodename viewname|imagetype"`` descriptor.  

Settings for the outpost detector
=================================
 
First, please note that there is no error checking or validation provided for any of the
settings described below. Any misconfiguration can result in operational failures. In most cases,
default values are not available. 

There is no incentive to configure more than a single ``outpost`` detector per camera view.  
The ``ROI`` setting is only used for restricting motion detection. A ``spyglass`` and all of the
object detection and tracking analysis it provides always applies to the full size camera image.  

spyglass
--------

This is a critically important setting. 

Since the ``SpyGlass`` runs in a separate process, a shared memory buffer is allocated for passing 
the full size image for analysis. This buffer must be sized properly or the operation will fail.

This setting specifies a tuple with the dimensions of the camera image being passed through the 
**imagenode** pipeline. This should match the setting for the camera ``resolution`` value in the
YAML configuration file.  

.. code-block:: yaml

  spyglass: (640, 480)   # important, must match camera "resolution" setting

*Caution*. This is not an ideal, so a word to the wise. The **imagenode** pipeline might be carrying
an image sized differently than the camera setting. An example of this is the ``resize_width`` 
configuration item. That one should always be avoided when running an ``Outpost`` since it is
so computationally expensive. 

*Sidebar*. It is always important to understand the performamnce impact of any other detectors
configured to run on an **Outpost** node.

*Just be careful out there*.

    **Why is this particular setting needed, anyway?**  The initialization for a  ``Detector``
    happens prior to the completion of camera startup. Only after camera initialization will 
    the ``Camera`` instance have learned and stored the true image size. The only alternative 
    to requiring this setting in the YAML file would be to delay the ``SpyGlass`` intialization 
    until the first image is presented. Not ideal.
    
    More to the point though, do not guess. When setting up an **Outpost** node, always exercise 
    due dilligence. Configure thoughtfully, test carefully, and confirm results. Determine the
    true image size being passed through the pipeline, and specify it here. 

ROI
---

Motion detection can be restricted to a smaller rectangular region of interest 
within the full size image. 

The ROI is described like an OpenCV (X1,Y1),(X2,Y2) rectangle, except that corners
are specified in percentages of full frame size rather than the number of pixels.
These values are the coordinates of the top left corner, followed by the coordinates
of the bottom right corner. Each corner is a tuple where the first number specifies
the distance from the left edge of the frame and the second value specifies the distance
from the top edge of the frame.

These numbers are given in integer percent values, from 0 to 100, of the image size. This
convention allows the ROI corners to remain the same even if the image capture resolution
is increased or decreased.

A value of (0,0),(100,100) would specify an ROI that is the full image. This is the
default if not explicitly specified.

.. code-block:: yaml

  ROI: (10,20),(70,80)   # region of interest for motion detection

Additional **imagenode** optional settings helpful for debugging and for tuning camera
and detector settings. 

.. code-block:: yaml

  draw_roi: ((255,0,0),1)   # draw the ROI box in blue with a line 1 pixel wide
  draw_time: ((255,0,0),1)  # timestamp text is blue with 1 pixel line width
  draw_time_org: (5,5)      # timestamp text starts at this (x,y) location 
  draw_time_fontScale: 1    # timestamp fontScale factor is 1

For furter information regarding these settings, please refer to
*"Camera Detectors, ROI and Event Tuning"* in
`imagenode Settings and YAML files 
<https://github.com/shumwaymark/imagenode/blob/master/docs/settings-yaml.rst>`_,
which provides additional details and background information.

tracker
-------

This setting selects the object tracking algorithm to use. The following subset of the 
OpenCV legacy contributed object trackers are supported.    

``boosting``
  A rather old AdaBoost implementation that has been superceded by faster algorithms.

``mil``
  Multiple Instance Learning. An improvement on the BOOSTING tracker, though faster 
  techniques such as KCF are now available.

``kcf`` 
  Kernelized Correlation Filters. Builds on the concepts of BOOSTING and MIL, faster
  and more accurate than both.

``tld``
  Tracking, Learning, and Detection. A self-correcting implementation that might work 
  well in certain scenarios. 

``medianflow``
  Compares references across time, excels at identifying tracking failures.

``mosse``
  Minimum Output Sum of Squared Error. Uses an adaptive correlation filtering technique 
  that is both accurate and fast.
  
``csrt``
  Discriminative Correlation Filter with Channel and Spatial Reliability. A very accurate 
  tracking algorithm with a trade-off of slightly slower operation. 

The general consensus on these seems to be that KCF is likely the best all around choice. The
CSRT tracker is more accurate though slightly slower. While MOSSE is very fast with some loss 
in accuracy.

.. code-block:: yaml

  tracker: kcf  # [csrt, kcf, boosting, mil, tld, medianflow, mosse]

skip_frames
-----------

Once objects are in view, the correlation tracking alogorithm specified above is used to track 
movement from one frame to the next. This tends to improve efficiency, since object detection is 
a relatively expensive operation in terms of CPU resources relative to object tracking. 

This setting controls the frequency for which object detection is re-applied to the view, measured by
a tick count for the **outpost**. The value specified here is not based on the number of frames actually
analyzed by the ``SpyGlass``.  This trigger is measured against the number of frames which have passed 
through the outpost for publishing, including those not analyzed.

.. code-block:: yaml

  skip_frames: 30

detectobjects
-------------

Object detection algorithm to use. Only YOLOv3 and MobileNetSSD have been implemented.
More to come later. YOLOv3 *is not recommended due to performance concerns*.

.. code-block:: yaml

  detectobjects: mobilenetssd  # [mobilenetssd, yolov3]

mobilenetssd
------------

This is used to specify the configuration for the MobileNetSSD object detector. Required 
when ``mobilenetssd`` is specifed for object detection.

.. code-block:: yaml

  mobilenetssd:
    prototxt_path: /home/pi/imagenode/outpost/mobilenet_ssd/MobileNetSSD_deploy.prototxt
    model_path: /home/pi/imagenode/outpost/mobilenet_ssd/MobileNetSSD_deploy.caffemodel
    confidence: 0.5
    target: cpu     # [cpu, myriad]          

yolov3
------

This is used to specify the configuration for the YOLOv3 object detector. Required 
when ``yolov3`` is specifed for object detection.

.. code-block:: yaml

  yolov3:
    yolo_path:  /home/pi/imagenode/outpost/yolo-coco
    confidence: 0.5
    threshold:  0.3
    consider: [person, car, truck, dog, cat, bird, bicycle, motorbike] 


Logging for tracking events
===========================

There are three tracking events reported by the ``outpost``. There is a single reported item for the
start of each event, and another at the end. The third reporting point is the tracking data itself, 
which is published repetitively across multiple frames throughout the lifespan of the event, for 
each frame reviewded and tracked object within. All of the data being reported for these three 
conditions is published over the logger in JSON format.

Each tracking message is associated with a specific event and camera view. The ``id`` field serves as the 
event identifier, this is a UUID value for uniqueness. The ``view`` field contains the configured ``viewname`` 
for the ``camera``. Note that the ``node`` name is not included in these messages since it is already being 
passed as the root topic of the logger. This pairing of node and view allows the **camwatcher** to differentiate 
between messages when subscribing to multiple *outpost* nodes simultaneously.

The third common field is the ``evt`` field, which can contain one of three values as described below. 

For efficiency, a timestamp is not currently included in these messages. Timestamps must be added by the
receiving system. Admittedly, this is less than accurate. However, as long as the end-to-end pipeline is 
opertaing efficiently, there should be at most just a few milliseconds of latency between the actual 
time of the observation, and the logged/reported time. 

1) Event start, the ``evt`` field contains the text ``start``. This message is sent once, when
   the tracking event begins. The ``fps`` field reflects the velocity of the **outpost** pipeline
   at the start of the event in frames per second. This value is calculated based on a rolling 
   average looking back over the previous 160 frames. A reported rate of 32 frames/second would 
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
   The ``obj`` and ``class`` fields contain an object identifier and classification name if available.
   The ``rect`` field has the x1,y1,x2,y2 corners of the bounding rectangle for the object being reported. 

   .. code-block:: json

     {
       "view": "PiCamera",
       "id": "42fc4bb46cc611ebb942dca63261a32e",
       "evt": "trk",
       "obj": 999999,
       "class": "person",
       "rect": [0, 0, 0, 0]
     }

3) End of the event, the ``evt`` field contains the text ``end``. Any other fields contained in the 
   structure beyond what is portrayed in the example below should be ignored. There could be extraneous 
   data carried in this message left over from the prior tracking event. 

   .. code-block:: json

     {
       "view": "PiCamera",
       "id": "42fc4bb46cc611ebb942dca63261a32e",
       "evt": "end"
     }

Outpost implementation
======================

*placeholder*

Changes to Python source code
-----------------------------

*more to come on this later* 

The new ``imagenode/sentinelcam`` folder has the Python code modules needed, and all changes
to the baseline, as detailed below, can be found in ``imagenode/tools/imaging.py`` 

.. code-block:: 

  imagenode
  ├───docs
  ├───imagenode
  │   ├───sentinelcam
  │   └───tools
  ├───outpost
  ├───tests
  └───yaml  

*import tooling for the outpost*

.. code-block:: python

  from sentinelcam.outpost import Outpost # SentineCam outpost support

*initializaton hook for the Detector instance*

.. code-block:: python

  elif detector == 'outpost':
    self.outpost = Outpost(self, detectors[detector], nodename, viewname)
    self.detect_state = self.outpost.object_tracker

That is all.

Legacy OpenCV contributed object trackers
-----------------------------------------

Note regarding more recent versions of the OpenCV library. The object tracking code
within OpenCV is currently being updated and refactored. The legacy contributed object
trackers have been moved into an ``OpenCV.legacy`` library.  The **spyglass** module 
as posted, currently still specifies the original hooks.

Provisioning the Outpost
------------------------

*more to come later regarding model deployment*

SentinelCam deployment
======================

*placeholder*

=====================
Notes on imagehub use
=====================

There are no modifications needed to the **imagehub** module. All planning and design goals provide 
for full support and compatibility with Jeff's *Librarian*. Any *outpost* node should be able to 
provide not only video and log publishing functionality, but also host any other sensors which conform 
to the Ying Yang Ranch design pattern.

`Return to main documentation page README <../README.rst>`_
