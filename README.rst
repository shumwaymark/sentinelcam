=========================================
sentinelcam: Smart Home Vision Technology
=========================================

Introduction
============

**SentinelCam** is an unfinished work in progress. The project goal is to develop a small-scale
distributed facial recognition and learning pipeline hosted on a network of Raspberry Pi computers.
The practical application for this is to build a stand-alone embedded system served by multiple
camera feeds that can easily support presence detection within the context of smart home automation.

.. contents::

Initial project goals
=====================

Early project goals are to be able to recognize people and vehicles that are known to the house.
Differentiating between family, friends, guests, neighbors, *and strangers*. Identifying package and 
mail delivery. Knowing when a strange car has pulled into the driveway.

Significantly, any unknown face should automatically be enrolled and subsequently recognized going 
forward. Unknown faces can always receive a "formal introduction" later by tagging and categorizing
as desired.

- Able to operate independently of any cloud-based services or externally hosted infrastructure 
- Automatic video capture should be triggered by motion detection and stored for review/modeling
- Motion detector will provide basic object tracking for the duration of the event
- Object ids and associated tracking centroids are logged as an outcome of motion detection
- A live video feed from each camera must be available for on-demand viewing as desired  
- Video playback should support an optional timestamp and any desired labeling of inference results
- Optional time-lapse capture 

High-level design concept
=========================

The birds-eye overview of the early conceptual framework is portrayed by the following sketch. 

Multiple *outposts* are each a camera node. These are not rigged with internal disk storage.
One or more *data aggregators* are responsible for accumulating reported data and capturing
video streams. 

Realtime analysis of logged data from each *outpost* feeds a *dispatcher* responsible for
submitting tasks to the *sentinel*. Inference and labeling tasks should be prioritized over
modeling runs. The *sentinel* will need to be provisioned with adequate memory and computing
resources. 

.. image:: docs/images/SentinelCamOverview.png
   :alt: SentinelCam conceptual overview

One of the biggest challenges to implementing a workable solution to this problem, operating 
over a network of small single board computers such as the Raspberry Pi, is making effective 
use of the limited resources available.

This is best served by a "divide and conquer" approach. Spread out the workload for efficiency,
employing parallelization where helpful for processing incoming data sets. Keep overhead to a 
minimum. Each node in the network should serve a distinct purpose. Take care, do not overburden 
any individual node, while watching out for untapped idle capacity. Orchestration is key.

Although each *outpost* node operates independently, any detected event could be directly
related to an event being simultaneously processed by another node with an overlapping or 
adjacent field of view.

Object tracking references and related timestamps become the glue that ties inference results
back to the original source video streams. 

Leveraging imagenode and imagehub
=================================

Fortunately, early research led to the `imageZMQ <https://github.com/jeffbass/imagezmq>`_ 
library authored by Jeff Bass. This was key to resolving data transport issues between
nodes. 

For building out both the *outpost* and **camwatcher** functionality, it quickly became 
obvious that his **imagenode** and **imagehub** projects could provide scaffolding that 
was both structurally sound and already working.

Both projects have been forked as submodules to the **SentinelCam** project. Further 
details on how these modules have been adapted is documented in
`YingYangRanch_Changes <docs/YingYangRanch_Changes.rst>`_.

Currently, the complete functionality of the **SentinelCam** *outpost* has been entirely
encapsulated by **imagenode**. 

Project status
==============

**SentinelCam** is an incomplete, and largely experimental, work in progress. 

Camwatcher design
-----------------

A working draft of the **camwatcher** functionality is up and running. In its current 
state, this is best evaluated as an early working proof of concept. The diagram below
presents a high-level design sketch.

.. image:: docs/images/CamWatcher.png
   :alt: Sketch of early camwatcher design

This design exploits two enhancements made to the **imagenode** module: log and video 
publishing over ZMQ as configurable options.

Logfile publishing serves two purposes. It first allows error and warning conditions to be
accumulated in a centralized repository as they occur. This avoids reliance on SD cards
with limited storage capacity which could be dispersed across potentially dozens of
individual camera nodes. More importantly, logged event notifications and information 
related to an event in progress are then available as data which can be streamed to multiple
interested consumers in real time.

Video publishing also has a twofold benefit. Not only can video capture be quickly triggered
by an event in progress, a live video stream can simultaneously feed one or more monitors for
display as desired.

The **camwatcher** employs a Python ``asyncio`` event loop running a set of coroutines with
the following tasks.

- *Control Loop*. Uses a ZMQ Req/Rep port for receiving control commands. This currently 
  just allows an **imagenode** to route a notification during initialization to insure that
  a logfile subscription has been established. 

- *Log Subscriber*. Subscribes to logging data streamed from one or more **imagenode**
  publishers via ZMQ. Logging data that pertains to a video event is directed to the 
  *Dispatcher* for handling. Any other data is passed to the **camwatcher** internal logger.

- *Dispatcher*. Handles object tracking event data. For each new event, a video stream 
  subscriber is instantiated to begin capturing the video. All event tracking data is queued
  for permanent storage by the *CSV File Writer*.

This design packs a fair amount of network I/O activity into a single thread of execution. To 
best exploit the multi-core architecture of the Raspberry Pi 4B, a child process is forked to
capture and store the published video stream from each detected tracking event.

The *CSV File Writer* runs in a separate thread of execution. This component is responsible for
receiving queued data events and writing them into CSV-format text files based on the following 
data model.

Data model
----------

The data model is still in its infancy and continues to evolve. Two types of data are collected
by the **camwatcher**. Data related to the analysis of the event, and captured video images. All 
data is stored in the filesystem, within a separate folder for each category. 

Event tracking data and results from event analysis are written to the filesystem as a set of 
CSV-format text files. For each date, there is an event index file and a separate file with
the detailed data for each event.

The index file for each date folder is named ``camwatcher.csv`` as described below. There is no 
*header row* included in the data. This data structure is fixed, with no further changes expected.

  .. csv-table:: Event Index 
    :header: "Name", "Type", "Description"
    :widths: 20, 20, 60

    node, str, node name  
    viewname, str, camera view name 
    timestamp, datetime, timestamp at the start of the event
    event, str, unique identifer for the event 
    fps, int, pipeline FPS at start of event
    type, str, event type 

Event detail files always include a header row, with varying data structures depending on the type 
of event. There is currently only a single event type defined, the tracking events. The naming
convention for all detail files is: ``EventID_TypeCode.csv``

  .. csv-table:: Tracking Event Detail
    :header: "Name", "Type", "Description"
    :widths: 20, 20, 60

    timestamp, datetime, timestamp when tracking record written
    objid, int, object identifier
    centroid_x, int, object centroid X-coordinate
    centroid_y, int, object centroid Y-coordinate

These CSV files are written into the folder specified by the ``csvdir`` configuration setting and 
organized by date into subfolders with a YYYY-MM-DD naming convention.

.. code-block:: 

  csvdir
  ├── 2021-02-11
  │   ├── camwatcher.csv
  │   ├── 0b98da686cbf11ebb942dca63261a32e_trk.csv
  │   ├── 109543546cbe11ebb942dca63261a32e_trk.csv
  │   ├── 1fda8cb26cbd11ebb942dca63261a32e_trk.csv
  │   ├── 202cda206cbe11ebb942dca63261a32e_trk.csv
  │   ├── 7bf2ba8c6cb911ebb942dca63261a32e_trk.csv
  │   ├── a4f355686cbe11ebb942dca63261a32e_trk.csv
  │   ├── cde802a06cc011ebb942dca63261a32e_trk.csv
  │   ├── d1995d346cb811ebb942dca63261a32e_trk.csv
  │   └──  # etc, etc. for additional events
  ├── 2021-02-12
  │   ├── camwatcher.csv
  │   ├── 11ddcf986d6211ebb942dca63261a32e_trk.csv
  │   ├── 1af4aac66d5c11ebb942dca63261a32e_trk.csv
  │   ├── 1dd50b3a6d4a11ebb942dca63261a32e_trk.csv
  │   ├── 27f4b4686d3f11ebb942dca63261a32e_trk.csv
  │   ├── 3ce8389c6d3d11ebb942dca63261a32e_trk.csv
  │   └──  # etc, etc. for additional events
  │
  └──  # additional directories for each date

Captured video streams are written to the filesystem as individual image frames compressed into
JPEG files. These files are written into the folder specified by the ``outdir`` configuration
setting and organized by date into subfolders with a YYYY-MM-DD naming convention.

The file name convention for each stored frame is: ``EventID_TimeStamp.jpg`` as portrayed below.

.. code-block:: 

  outdir
  ├── 2021-02-11
  │   ├── 109543546cbe11ebb942dca63261a32e_2021-02-11_23.08.34.542141.jpg
  │   ├── 109543546cbe11ebb942dca63261a32e_2021-02-11_23.08.34.572958.jpg
  │   ├── 109543546cbe11ebb942dca63261a32e_2021-02-11_23.08.34.603971.jpg
  │   ├── 109543546cbe11ebb942dca63261a32e_2021-02-11_23.08.34.635492.jpg
  │   ├── ...
  │   ├── a4f355686cbe11ebb942dca63261a32e_2021-02-11_23.12.43.274055.jpg
  │   ├── a4f355686cbe11ebb942dca63261a32e_2021-02-11_23.12.43.305151.jpg
  │   ├── a4f355686cbe11ebb942dca63261a32e_2021-02-11_23.12.43.336279.jpg
  │   ├── a4f355686cbe11ebb942dca63261a32e_2021-02-11_23.12.43.367344.jpg
  │   ├── a4f355686cbe11ebb942dca63261a32e_2021-02-11_23.12.43.399926.jpg
  │   ├── a4f355686cbe11ebb942dca63261a32e_2021-02-11_23.12.43.429276.jpg
  │   ├── a4f355686cbe11ebb942dca63261a32e_2021-02-11_23.12.43.459129.jpg
  │   ├── a4f355686cbe11ebb942dca63261a32e_2021-02-11_23.12.43.490918.jpg
  │   └──  # etc, etc. for additional images
  ├── 2021-02-12
  │   ├── 11ddcf986d6211ebb942dca63261a32e_2021-02-12_18.42.33.998836.jpg
  │   ├── 11ddcf986d6211ebb942dca63261a32e_2021-02-12_18.42.34.028291.jpg
  │   ├── 11ddcf986d6211ebb942dca63261a32e_2021-02-12_18.42.34.060119.jpg
  │   ├── 11ddcf986d6211ebb942dca63261a32e_2021-02-12_18.42.34.093632.jpg
  │   ├── 11ddcf986d6211ebb942dca63261a32e_2021-02-12_18.42.34.124754.jpg
  │   ├── 11ddcf986d6211ebb942dca63261a32e_2021-02-12_18.42.34.154909.jpg
  │   └──  # etc, etc. for additional images
  │
  └──  # additional directories for each date

It is important to note that the capture rate for frames of video can vary significantly from
the rate of capture for the tracking data. To correlate tracking data back to a captured image,
it is necessary to bind these together by estimating an elapsed time from the start of the event 
for each data source.

Research and development roadmap
================================

Development is proceeding on several fronts simultaneously. The categories below do not
describe an all-inclusive list, they are simply interrelated areas of current focus. The 
conceptuaL framework driving the overall project is larger in scope. Updates are published
here on an incremental basis as new functionality is fleshed out, proven, and stabilized. 

Sentinel
--------

The *sentinel* module is conceived as the inference and modeling engine. This will ultimately
be the heart of the system. One or more *dispatchers* are responsible for firing events that
are deemed worthy of deeper analysis by the *sentinel*. 

Dynamic task scheduling of batch jobs is a critcal aspect of this. The ability to analyze 
ongoing events in something close to real time is of utmost importance. Therefore, inference
and labeling tasks are the highest priority; modeling and reinforcement more secondary. 

Outpost
-------

Beyond simple motion detection and object tracking, some inference tasks can be pushed out to
the edge where appropriate and helpful. Applying object identification across a sampling of
incoming frames could help determine whether a motion event is deserving of prioritized analysis
by the *sentinel*.

Taken a leap further, selected camera nodes can be equipped with a coprocessor such as the
Google Coral USB Accelerator or Intel Neural Compute Stick. This can allow for running a facial
recognition model directly on the *outpost* itself. When focused on an entry into the house,
any face immediately recognized would not require engaging the *sentinel* for further analysis.

Data management
---------------

There are two aspects to data management requirements: event analysis, and cataloging results.

For storing end results in a manner that facilitates effective retrieval, the primary concerns
are what happened when and can those determinations be easily associated back to the source 
video stream. 

Raw data gleaned from a video event can be voluminous and detailed, especially if analyzing each
individual frame. There can be multiple objects of interest moving through the field of view
simultaneously. Data elements collected could include the geometric centroid, bounding coordinates,
direction and velocity of travel, and a unique identifer for each object. Blended into this might
be the aggregated results inferred from one or more deep neural networks. Assuming an ideal video
capture rate of near 30 frames per second, this can obviously add up in a hurry.

Effective and efficient data analysis of a video event thus presents challenges. Current research
into a solution leans heavily towards a reliance on the pandas library as the vehicle of choice
for getting data into, and out of, each model. 

Video event playback
--------------------

The ability to easily select and review historical events and then present them within a video
player is an obvious requirement. This will ultimately evolve into a set of services to search 
for, list, and replay events that have been cataloged. 

Additional documentation
========================
- `Version History and Changelog <HISTORY.md>`_
- `Changes to imagenode and imagehub projects <docs/YingYangRanch_Changes.rst>`_
- `Development blog <https://blog.swanriver.dev>`_

Technology foundation
=====================

**SentinelCam** is being developed and tested on top of the following core technologies
and libraries.

- Raspberry Pi 4B
- Raspbian Buster
- picamera
- Python 3
- OpenCV 4
- PyZMQ
- imageZMQ
- imutils
- numpy
- pandas

Acknowledgements
================

- Dr. Adrian Rosebrock and the PyImageSearch team; his book: *Raspberry Pi for Computer Vision* 
  has been an invaluable resource.
- Jeff Bass (imagezmq, imagenode, and imagehub); his outstanding work has allowed this project
  to get off to a fast start.
