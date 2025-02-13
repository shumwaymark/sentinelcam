============================
Changes to imagenode project
============================

.. contents::

Overview
========

The **SentinelCam** project has relied heavily on the **imagenode** project as an initial 
foundation to build upon. 

The **imagenode**, **imagehub**, and **librarian** application suite offers already working, 
readily exploitable, solutions for camera image capture, analysis, and reporting. The hub and spoke 
architecture dovetails nicely with the **SentinelCam** design. The **imagenode** module includes 
many additional features for handling other types of sensors and vision tasks not requiring video 
production.

Only the **imagenode** module has been modified for this project. Significant changes consist of adding
support for log and image publishing over ZMQ as configurable options. The ``Outpost`` detector is
a multiprocessing solution using shared memory for passing image data. Details regarding implementation
and operation are documented below.

Please note that the **imagenode** module must always be paired with an **imagehub**, which continues 
to fulfill its existing role without change.

   For complete details on the configuration and use of an **imagenode** deployment, please refer 
   to the `baseline documentation <https://github.com/shumwaymark/imagenode/blob/master/README.rst>`_

==========================
Modifications to imagenode
==========================

Modifications to **imagenode** are primarily encapsulated by the implementation of the ``outpost`` detector.
There are two aspects to this effort. Providing support for log and image publishing, and developing an 
object tracker to fuel the **camwatcher** engine.

Example configuration with new options
======================================

An expanded example ``imagenode.yaml`` file is provided below to demonstrate how the new configuration
options are specified.

.. code-block:: yaml

  # Settings file imagenode.yaml -- for a sentinelcam outpost, version 7 
  ---
  node:
    name: outpost
    heartbeat: 15
    REP_watcher: False            # Not recommended for sentinelcam
    queuemax: 50
    send_type: jpg
    send_threading: True
    print_settings: False
  hub_address:
    H1: tcp://data1:5555
  cameras:
    P1:
      viewname: PiCam3
      resolution: (640, 480)
      framerate: 32
      threaded_read: False         # use direct image retrieval through picamera2 library
      vflip: False
      detectors:
          outpost:
              publish_cam: 5567        # ZMQ port for image frame publishing
              publish_log: 5565        # ZMQ port for log publishing, must match logconfig below
              logconfig:               # logging configuration dictionary 
                  version: 1
                  handlers:
                      zmq:
                          class: zmq.log.handlers.PUBHandler
                          interface_or_socket: tcp://*:5565
                          root_topic: outpost
                          level: INFO
                  root:
                      handlers: [zmq]
                      level: INFO
              camwatcher: tcp://data1:5566   # optional self-introduction to a running camwatcher 
              spyglass: (640, 480)           # important, must match camera "resolution" above
              detectobjects: mobilenetssd    # [mobilenetssd, yolov3]
              accelerator: none              # [none, ncs2, coral]
              tracker: none                  # [none, dlib csrt, kcf, boosting, mil, tld, medianflow, mosse]
              skip_factor: 7                 # (only relevant when a tracker is also specified)
              mobilenetssd:
                  prototxt_path: /home/ops/imagenode/outpost/mobilenet_ssd/MobileNetSSD_deploy.prototxt
                  model_path: /home/ops/imagenode/outpost/mobilenet_ssd/MobileNetSSD_deploy.caffemodel
                  confidence: 0.5
                  target: cpu                # [cpu, myriad]          
              yolov3:  
                  yolo_path: /home/ops/imagenode/outpost/yolo-coco
                  confidence: 0.5
                  threshold: 0.3
                  consider: [person, car, truck, dog, cat, bird, bicycle, motorbike] 
              ROI: (10,20),(70,80)
              draw_roi: ((255,0,0),1)
              draw_time: ((255,0,0),1)  
              draw_time_org: (5,5)  
              draw_time_fontScale: 0.5 
  # Other cameras, detectors, and sensors can be supported - such as for ambient temperature: 
  sensors:
    T1:
      name: Temperature
      type: DS18B20
      gpio: 4
      read_interval_minutes: 10
      min_difference: 1

IMPORTANT - Note regarding status of migration to picamera2
===========================================================

As part of the ongoing migration to current software versions for operating system and supporting 
application libraries, the ``picamera`` library is being replaced with ``picamera2``.

Currently, the **imagenode** version in use here includes only an interim migration to the new library. 
For further details see comments below regarding changes to python source code.

The legacy configuration options to support camera settings such as exposure, contrast, shutter
speed, white balance, etc. were all implemented via the original `picamera` library. These options
have been abandoned by this shortcut. All cameara settings, except for ``resolution`` and ``framerate``, 
are *untested and assumed to be broken*. 

Camwatcher connection settings
==============================

There are three basic configuration settings related to communication with the **camwatcher**.

.. code-block:: yaml

  publish_cam: port nunber to use for image frame publishing 
  publish_log: port number to use for log publishing
  logconfig:   logging configuration dictionary

publish_cam
------------

The ``publish_cam`` setting takes a single argument: a numeric port number. This is used to
activate image publishing as an ``imagezmq.ImageSender``. Each image passing through the pipeline 
for the camera is published. This allows any client to subscribe as an ``imagezmq.ImageHub`` for 
access to a live camera feed as needed.

Each frame is published as a JPEG-compressed image. The publishing frame rate depends on the
length of the vision processing pipeline of the **imagenode**. Multiple cameras, large image
sizes, additional detectors, and processing complexity, can each have compounding adverse 
effects on the velocity out to the client endpoint for capture or display.

publish_log
-----------

The ``publish_log`` setting also has a numeric port number argument. Used for log publishing 
over 0MQ. Should match the value specified in the ``logconfig`` dictionary below. 

logconfig
---------

Required configuration dictionary for logging over a ZeroMQ PUB socket. Once activated, all calls 
to the logger use this mechanism. A few notes on configuration...

.. code-block:: yaml

  interface_or_socket: Local connection string for binding to the socket; see "publish_log" above
  root_topic:          Must match the node name specified at the top of the YAML file
  level:               INFO is required for basic outpost functionality

The ``root_topic`` for the logger should match the configured node name from the top of the YAML file. A 
logging level of INFO is required to support basic functionality, though DEBUG can be used when needed. The
connection string specified for ``interface_or_socket`` should specify a port number that matches the value
given for ``publish_log``.

camwatcher
----------

This is an optional configuration item which can be used to introduce an outpost node to a running **camwatcher**
instance. Production deployments include **camwatcher** configurations with connection strings for the specific 
list of outpost nodes which should always be established. This option provides for a dynamic, though 
temporary, introduction. A restart of the camwatcher will clear any such ad hoc outpost registrations. 

During startup, a JSON-encoded camera introduction message is constructed and sent to the **camwatcher** 
control port specified in this connection string. The camwatcher will establish subscriptions and note the
node and view of the new outpost. 

  The following camera handoff message reflects the example YAML configuration file presented earlier.
  Connection strings for the log and image publishers are constructed based on the port numbers specified
  for ``publish_log`` and ``publish_cam``, along with the actual hostname of the node learned from the
  running network configuration.

.. code-block:: json

   {
      "cmd": "CamUp",
      "node": "outpost",
      "view": "PiCam3",
      "logger": "tcp://lab1:5565",
      "images": "tcp://lab1:5567"
   }

--------------------------------
Publishing with multiple cameras
--------------------------------

The publishing settings described above are only applied once per **imagenode**. This insures 
that any given node will have only a single logging publisher and single image publisher, each
binding to a single port.

It may be desirable to have multiple cameras on a individual node, each with a different perspective. 

When using multiple cameras, only the port number specified for the first entry in the YAML file 
is used for publishing. Port numbers on any additional setup entries are ignored. Keep these the 
same for consistency in such cases to help reduce confusion when reviewing the configuration.

Be aware that when simultaneously publishing from multiple cameras on any individual node, image
frames from each camera will be interleaved in the stream. The **camwatcher** is aware of this, 
and always filters by ``viewname`` when subscribing to an image stream. 

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

*Sidebar*. It is always important to understand the performance impact of any other detectors
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

This setting selects the object tracking algorithm to use. *Deprecated*

``none``
  *Current recommendation*. Tracking logic as originally implemented to be scrapped and redesigned.

``dlib``
  Use the dlib correlation tracker. *Required if contributed trackers below are not available*.

The following subset of the OpenCV legacy contributed object trackers are also supported.

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

  tracker: none  # [none, csrt, kcf, boosting, mil, tld, medianflow, mosse]

skip_factor
-----------

Once objects are in view, the correlation tracking algorithm specified above is used to track 
movement from one frame to the next. The goal is to improve efficiency, since object detection is 
a relatively expensive operation in terms of CPU resources relative to object tracking. 

This setting controls the frequency for which object detection is re-applied to the view, measured by
a tick count for the **outpost**. The value specified here is not based on the number of frames actually
analyzed by the ``Outpost``. This trigger is measured against a cycle count for the image processing 
pipeline.

  *Understanding the best value to use for this, now deprecated, setting requires more art and magic 
  than what should be appropriate. Clearly not a reasoned, well-understood factor*.

.. code-block:: yaml

  skip_factor: 13

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
    prototxt_path: /home/ops/imagenode/outpost/mobilenet_ssd/MobileNetSSD_deploy.prototxt
    model_path: /home/ops/imagenode/outpost/mobilenet_ssd/MobileNetSSD_deploy.caffemodel
    confidence: 0.5
    target: cpu     # [cpu, myriad]          

yolov3
------

This is used to specify the configuration for the YOLOv3 object detector. Required 
when ``yolov3`` is specifed for object detection.

.. code-block:: yaml

  yolov3:
    yolo_path:  /home/ops/imagenode/outpost/yolo-coco
    confidence: 0.5
    threshold:  0.3
    consider: [person, car, truck, dog, cat, bird, bicycle, motorbike] 

Logging for tracking events
===========================

There are three tracking events reported by the ``outpost``. There is a single reported item for the
start of each event, and another at the end. The third reporting point is the tracking data itself, 
which is published repetitively across multiple frames throughout the lifespan of the event, for 
each frame reviewed and tracked object within. All of the data being reported for these three 
conditions is published over the logger in JSON format.

Each tracking message is associated with a specific event and camera view. The ``id`` field serves as the 
event identifier, which is a UUID value for uniqueness. The ``view`` field contains the configured ``viewname`` 
for the ``camera``. Note that the ``node`` name is not included since it is already being passed as the root 
topic of the logger. This pairing of node and view allows the **camwatcher** to differentiate between messages 
when subscribing to an outpost node supporting multiple views.

The third common field is the ``evt`` field, which can contain one of three values as described below. 

1) Event start. This message is sent once, when the tracking event begins. The ``fps`` field reflects 
   the velocity of the **outpost** image publisher at the start of the event in frames per second. This 
   value is calculated as a rolling average over a moving window across the prior few seconds. 

   .. code-block:: json

     {
       "evt": "start",
       "view": "PiCam3",
       "id": "42fc4bb46cc611ebb942dca63261a32e",
       "timestamp": "2024-10-15T07:32:12.856029",
       "camsize": [640, 480]
       "fps": 31.7
     }

2) Object tracking data. This message is sent multiple times while the event is in progress, for each 
   analyzed frame and tracked object within the frame. The ``obj`` and ``class`` fields contain an object 
   identifier and classification name if available. The ``rect`` field has the x1,y1,x2,y2 corners of the 
   bounding rectangle for the object being reported. 

   .. code-block:: json

     {
       "evt": "trk",
       "view": "PiCam3",
       "id": "42fc4bb46cc611ebb942dca63261a32e",
       "timestamp": "2024-10-15T07:32:12.856029",
       "obj": "xyzzy",
       "class": "person",
       "rect": [0, 0, 0, 0]
     }

3) End of the event. An optional list of tasks to be submitted to the **sentinel** will be included when 
   configured, based on detction results.

   .. code-block:: json

     {
       "evt": "end",
       "view": "PiCam3",
       "id": "42fc4bb46cc611ebb942dca63261a32e",
       "tasks": [
           ["sometask", 1], 
           ["anothertask", 1],
           ["sweep", 2]
        ]
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

Import tooling for the outpost...

.. code-block:: python

  from sentinelcam.outpost import Outpost, OAKcamera # SentineCam outpost support

Initializaton hook within the Camera instance for an OAK camera...

.. code-block:: python

      self.cam_type = 'PiCamera'
  elif camera[0].lower() == 'o':  # OAK camera
      self.cam = OAKcamera(self.viewname)
      self.cam_type = 'OAKcamera'
  else:  # this is a webcam (not a picam)

Initializaton hook for the Detector instance...

.. code-block:: python

  elif detector == 'outpost':
    self.outpost = Outpost(self, detectors[detector], nodename, viewname)
    self.detect_state = self.outpost.object_tracker

A hastily-coded rewrite of the PiCamera direct read to utilize the ``picamera2`` library.

  *Current status of all the legacy camera settings such as exposure, contrast, shutter speed, 
  white balance, etc. are undetermined and assumed to be broken.* These features were all 
  implemented with the original picamera library. Only the resolution and framerate should 
  be expected to work correctly at this time.

.. code-block:: python

  class PiCameraUnthreadedStream():
      def __init__(self, resolution=(320, 240), framerate=32, **kwargs):
          from picamera2 import Picamera2
          Picamera2.set_logging(Picamera2.INFO)
          self.camera = Picamera2()
          # setup the camera and start it
          self.camera.still_configuration.main.size = resolution
          self.camera.still_configuration.main.format = "RGB888"
          self.camera.still_configuration.buffer_count = 2
          self.camera.still_configuration.controls.FrameRate = framerate
          self.camera.configure("still")
          self.camera.start()
          self.frame = None

      def read(self):
          self.frame = self.camera.capture_array('main')
          return self.frame

      def stop(self):
          self.close()

      def close(self):
          None

That is all.

Legacy OpenCV contributed object trackers
-----------------------------------------

Note regarding more recent versions of the OpenCV library. The object tracking code
within OpenCV is currently being updated and refactored. The legacy contributed object
trackers have been moved into an ``OpenCV.legacy`` library which must be available for
their use.

Provisioning the Outpost
------------------------

*more to come later regarding model deployment*

SentinelCam deployment
======================

*placeholder*

`Return to main documentation page README <../README.rst>`_
