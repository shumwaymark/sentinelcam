=========================================
sentinelcam: Smart Home Vision Technology
=========================================

Introduction
============

**SentinelCam** is an unfinished work in progress. The project goal is to develop a small-scale
distributed facial recognition and learning pipeline hosted on a network of Raspberry Pi computers.
The practical application for this is to build a stand-alone embedded system served by multiple camera
feeds that can easily support presence detection within the context of smart home automation.

.. contents::

Initial project goals
=====================

Initial project goals are to be able to recognize people and vehicles that are known to the house.
Differentiating between family, friends, guests, neighbors, *and strangers*. Identifying package and 
mail delivery. Knowing when a strange car has pulled into the driveway.

Significantly, any unknown face should automatically be enrolled and subsequently recognized going 
forward. Unknown faces can always receive a "formal introduction" later by labeling and categorizing
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

Realtime analysis of logged data from each *outpost* drives a *dispatcher* responsible for
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

The functionality of the **SentinelCam** *outpost* has been completely encapsulated by
**imagenode**. 

This first draft of the **camwatcher** may appear to leave the **imagehub** without much
of a role. However, current planning conceptually provides for full integration and 
compatibility with Jeff's *Librarian*. Any *outpost* node should be able to provide not 
only video publishing functionality, but also host other sensors. 

Project status
==============

**SentinelCam** is an incomplete, and largely experimental, work in progress. 

camwatcher design
-----------------

A first draft of the **camwatcher** functionality is up and running. 

.. image:: docs/images/CamWatcher.png
   :alt: Sketch of camwatcher design


data model
----------


Development Roadmap
===================



Technology Foundation
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

Acknowledgements
================

- Adrian Rosebrock and the PyImageSearch team
- Jeff Bass (imagezmq, imagehub, and imagenode)
