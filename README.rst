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
- Object identifiers and associated tracking centroids are logged as an outcome of motion detection
- A live video feed from each camera must be available for on-demand viewing as desired  
- Video playback should support an optional timestamp and any desired labeling of inference results
- Optional time-lapse capture 

High-level design concept
=========================

The birds-eye overview of the early conceptual framework is portrayed by the following sketch. 

Multiple **Outposts** are each a camera node. These are not rigged with internal disk storage.
One or more *data aggregators* are responsible for accumulating reported data and capturing
video streams. 

Realtime analysis of logged data from each **Outpost** feeds a *dispatcher* responsible for
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

Although each **Outpost** node operates independently, any detected event could be directly
related to an event being simultaneously processed by another node with an overlapping or 
adjacent field of view.

Object tracking references and related timestamps become the glue that ties inference results
back to the original source video streams. 

   Ideally, this would be something hidden away in walls, cabinets and closets... and 
   then mostly forgotten about. Because it will just work, with limited care and feeding. 
   *Dream big, right?* 

Leveraging imagenode
====================

Fortunately, early research led to the `imageZMQ <https://github.com/jeffbass/imagezmq>`_ 
library authored by Jeff Bass. This was key to resolving data transport issues between
nodes. 

For building out the functionality of the **Outpost**, it quickly became obvious that 
his **imagenode** project could provide scaffolding that was both structurally sound and 
already working. This project has been forked as a submodule here. Additional details 
regarding the enhancements made to it are documented in 
`YingYangRanch_Changes <docs/YingYangRanch_Changes.rst>`_.

Most significantly, this enhanced **imagenode** module completely encapsulates all the
functionality required by the **Outpost**, while continuing to serve in its existing
role.

Project status
==============

**SentinelCam** is an incomplete, and largely experimental, work in progress. 

Outpost design
--------------

Imagine a lonely sentry standing guard at a remote outpost. Each outpost is positioned to watch over
the paths leading towards the inner fortifications. Sentries are tasked with observing, monitoring,
and reporting anything of interest or concern. Such reports should be sent back to central command
for analysis and descision making.

This analogy represents the underlying concept behind the **Outpost** design. Each node monitors the
field of view, watching for motion. Once motion has occured a ``SpyGlass`` is deployed for a closer
look. Whenever one or more recognizable objects have been detected, this is reported and motion through
the field of view tracked and logged.

The **Outpost** is implemented as a ``Detector`` for an **imagenode** camera. This allows it to easily
slip into the existing **imagenode** / **imagehub** / **librarian** ecosystem as supplemental functionality.

.. image:: docs/images/Outpost.png
   :alt: High-level sketch of Outpost integration with imagenode

Two key enhancements provide the essential wiring to make this possible. Log and image publishing over 
PyZMQ and imageZMQ respectively.

Image publishing has a twofold benefit.

- Image capture from another node can be quickly initiated by an event in progress.
- A live stream can simultaneously feed one or more monitors for on-demand real time display.

Images are transported as individual full-sized frames, each compressed into JPEG format. For 
smooth realistic video playback, the pipeline needs to run with a target thoughput of somewhere 
close to 30 frames per second, ideally.

Obtaining this goal on a Raspberry Pi can quickly become a signficant challenge when building out 
the pipeline with CPU-intensive tasks such as object identifcation and tracking.

To achieve the highest publishing frame rate possible, an **Outpost** node can employ a ``SpyGlass`` 
for closer analysis of motion events. The idea is to keep the pipeline lean for quickly publishing 
each frame, while processing a subset of the images in parallel to drive a feeedback loop. 
This spyglass is a multiprocessing solution. 

The following general strategy provides an overview of this technique.

- Motion detection is applied continually whenever there is nothing of interest within the field
  of view. This is a relatively quick background subtraction model which easily runs within the main 
  image processing pipeline.
- A motion event triggers the application of an object identification lens to the spyglass.
- Each object of interest is tagged for tracking.
- With objects of interest in view, a tracking lens is applied to subsequent frames whenever the 
  spyglass is not already busy.
- Object identification is periodically reapplied to refresh the tracking data.
- The newest image passing through the pipeline is only provided to the spyglass after results 
  from the prior task have been returned. This signals its availability for new work.

.. image:: docs/images/SpyGlass.png
   :alt: Outpost to Spyglass inter-process marshalling

This architecture potentially allows for increasingly sophisticated vision analysis models to be
deployed directly on an **Outpost** node. Specialized lenses could be developed for the ``SpyGlass``
based on the type of event and results from current analysis. The intent is to support the design
of a cascading algorithm to first inspect, then analyze a subset of selected frames and regions of
interest as efficiently as possible on multi-core hardware.

For example, if a person was detected, is there a face in view? If so, can it be recognized? Was it
package delivery or a postal carrier? If the object of interest is a vehicle, can the make/model be
deterimined? The color? Is there a license plate visible?

As a general rule, in-depth analysis tasks such as these are assigned to batch jobs running on the
**Sentinel** itself.

Log publishing also offers two benefits.

- Allows error and warning conditions to be accumulated in a centralized repository as they occur.
  This avoids reliance on SD cards with limited storage capacity which could be dispersed across 
  potentially dozens of individual camera nodes.
- More importantly, logged event notifications including information related to an event in progress
  are then available as data which can be streamed to multiple interested consumers in real time.

The **Outpost** as currently implemented is still highly experimental, and best represents proof 
of concept as working draft. Complete details on the design, structure, and operation of
the **Outpost** have been documented in `YingYangRanch_Changes <docs/YingYangRanch_Changes.rst>`_.

Camwatcher design
-----------------

A prototype of the **camwatcher** functionality is up and running in production. In its current
state, this is best evaluated as working proof of concept. The diagram below presents a high-level 
design sketch.

.. image:: docs/images/CamWatcher.png
   :alt: Sketch of basic camwatcher design

This design exploits two of the enhancements made to the **imagenode** module described
above supporting **Outpost** functionality: log and image publishing over ZeroMQ as 
configurable options.

The **camwatcher** employs a Python ``asyncio`` event loop running a set of coroutines with
the following tasks.

- *Control Loop*. Uses a ZeroMQ REQ/REP design pattern for receiving control commands. This 
  currently just allows an **Outpost** to route a notification during initialization to insure 
  that a logfile subscription has been established. 

- *Log Subscriber*. Subscribes to logging data streamed from one or more **Outpost**
  publishers via ZMQ. Logging data that pertains to a camera event is directed to the 
  *Dispatcher* for handling. Any other data is passed to the **camwatcher** internal logger.

- *Dispatcher*. Handles object tracking event data. For each new event, a subprocess is
  started as a image stream subscriber to begin capturing images. All event tracking data
  is queued for permanent storage by the *CSV File Writer*.

This design packs a fair amount of network I/O activity into a single thread of execution. To 
best exploit the multi-core architecture of the Raspberry Pi 4B, a child process is forked to
capture and store the published images from **Outpost** nodes while an event is in progress.

The *CSV File Writer* runs in the main process within a separate thread of execution. This component 
is responsible for receiving queued data events and writing them into CSV-format text files based 
on the following data model.

Data model
----------

The data model is still in its infancy and continues to evolve. Two types of data are collected
by the **camwatcher**. Data related to the analysis of the event, and captured images. All 
data is stored in the filesystem, within a separate folder for each category. 

Event tracking data and results from event analysis are written to the filesystem as a set of 
CSV-format text files. For each date, there is an event index file and a separate file with
the detailed data for each event.

All dates and timestamps reflect Coordinated Universal Time (UTC), not the local timezone.

The index file for each date folder is named ``camwatcher.csv`` as described below. There is no 
*header row* included in the data. This data structure is fixed, with no further changes expected.

.. csv-table:: Event Index 
  :header: "Name", "Type", "Description"
  :widths: 20, 20, 60

  node, str, node name  
  viewname, str, camera view name 
  timestamp, datetime, timestamp at the start of the event
  event, str, unique identifer for the event 
  fps, int, pipeline velocity at start of event
  type, str, event type 

Event detail files always include a header row, with varying data structures depending on the type 
of event. There is currently only a single event type defined, the tracking events. The naming
convention for all detail files is: ``EventID_TypeCode.csv``

.. csv-table:: Tracking Event Detail
  :header: "Name", "Type", "Description"
  :widths: 20, 20, 60

  timestamp, datetime, timestamp when tracking record written
  objid, str, object identifier
  classname, str, classification name
  rect_x1, int, bounding rectangle X1-coordinate
  rect_y1, int, bounding rectangle Y1-coordinate
  rect_x2, int, bounding rectangle X2-coordinate
  rect_y2, int, bounding rectangle Y2-coordinate

These CSV files are written into the folder specified by the ``csvdir`` configuration 
setting and organized by date into subfolders with a YYYY-MM-DD naming convention.

Although identifiers are unique, event data is always referenced by date. There is no event 
index crossing date boundaries. 

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

Captured images are written to the filesystem as individual full-sized frames 
compressed into JPEG files. These files are written into the folder specified 
by the ``outdir`` configuration setting and organized by date into subfolders 
with a YYYY-MM-DD naming convention.

This convention allows for retrieval and storage that is both fast and efficient 
on such small devices. Analysis tasks have speedy direct access to any desired 
event and point in time. The price paid for this includes a little extra network 
bandwidth when pulling the images down, and disk storage requirements which are 
best characterized as greedy. *Very greedy*.

The file name convention for each stored frame is: ``EventID_TimeStamp.jpg`` as 
portrayed below.

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

It is important to note that the collection of image data occurs independently from the tracking 
data. Some variation in the rate of capture can be expected. Differences from a perspective in real 
time are not expected to be signficant. To correlate tracking data back to a captured image, it is 
helpful to bind these together by estimating an elapsed time from the starting point for each data 
source, perhaps even with consideration for latency as an additional factor.

DataPump and DataFeed
---------------------

Collecting and storing data is only step number one. What logically follows, is easy access
for analysis. Once tasked with event review, the **sentinel** will be hungry for images and 
any tracking records generated by the outpost.

This potentially ravenous fast-food style appetite is to be fed with requests to a 
**Data Feed**. The Data Feed was conceived as a library to provide application programs with 
functions for accessing any desired set of images and tracking data produced from an outpost 
and collected by a **camwatcher**.

Thus both the ``DataFeed`` and ``DataPump`` classes, along with the **datapump** module, were born. 
The **datapump** is the stand-alone server process which responds to Data Feed access requests
over the network. Communication between components is via imageZMQ using a REQ/REP socket pair. 

.. code-block:: python

  class DataFeed(imagezmq.ImageSender):  # REQ socket - sends requests to a DataPump 
  class DataPump(imagezmq.ImageHub):     # REP socket - responds to DataFeed requests

Any module needing access to **camwatcher** data simply needs to create a ``DataFeed`` instance. 
The network address for a running **datapump** process is specified at that time.

.. image:: docs/images/DataFeed.png
   :alt: DataPump to DataFeed flow

The ``DataFeed`` and ``DataPump`` subclasses extend the imageZMQ base classes with support 
for sending and receiving both pandas DataFrame objects, and lists of timestamps. This helps 
keep everything in the same serialization context underpinning imageZMQ, with consistent
image transport technology throughout the system.

Internally, the first element of the (text, data) tuple returned to the Data Feed has been 
reserved for carrying a yet-to-be-implememted response code from the **datapump**. 

  **Status**: working proof of concept, still evolving.  

.. code-block:: python

  DataFeed.get_date_index (date) -> pandas.DataFrame

The ``get_date_index()`` function returns the content of the Event Index for a date. The date
parameter is always required and specified in 'YYYY-MM-DD' format. There is no default value.
The Event Index data is returned as a ``pandas.DataFrame`` obect. Refer to *Data Model* above 
for further detail.

.. code-block:: python

  DataFeed.get_tracking_data (date, event) -> pandas.DataFrame

The ``get_tracking_data()`` function requires two arguments, a date and an event identifier. 
Used to retrieve the full Tracking Event Detail dataset (see *Data Model* above) as a
``pandas.DataFrame`` object. Both arguments are required. The date is specified in 'YYYY-MM-DD'
format, the EventID reference must exist for the indicated date. There is no error-checking.

.. code-block:: python

  DataFeed.get_image_list (date, event) -> [timestamp]

This function provides a list of ``datetime.timestamp`` objects reflecting the capture times 
on images published by the Outpost. These are provided in chronological order. Function arguments 
are identical to what is described above for ``get_tracking_data()``.

All date and time references are in Coordinated Universal Time (UTC), not the local timezone.

.. code-block:: python

  DataFeed.get_image_jpeg (date, event, timestamp) -> bytes

Returns a buffer with the image frame as compressed JPEG data. Always for an existing date, 
event, and timestamp as descibed above. There is no error checking on this either. 

Presenting **camwatcher** data in this fashion provides the *Sentinel* with direct access to 
specific subsets of captured image data. For example, perhaps the images of interest are  
not even available until 3 seconds after the start of the event. This facilitates skipping
over the first 90-100 frames, for fast efficient access to the point of interest. 

Research and development roadmap
================================

Development is proceeding along multiple paths simultaneously. The categories below do not
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

Beyond simple object detection and tracking, some inference tasks can be pushed out to the
edge where appropriate and helpful. Applying more sophisticated models across a sampling
of incoming frames could help determine whether a motion event should be prioritized for
closer analysis by the *sentinel*. 

Additional performance gains can be achieved here by equipping selected outpost nodes with
a coprocessor, such as the Google Coral USB Accelerator or Intel Neural Compute Stick. Proper
hardware provisioning can allow for running facial and vehicle recognition models directly on
the camera node. When focused on an entry into the house, any face immediately recognized would
not require engaging the *sentinel* for further analysis.

Essentially, this could enable a camera to provide data in real time for discerning between
expected/routine events and unexpected/new activity deserving of a closer look.

Data management
---------------

There are several aspects to data management. For starters, it's a challenge. These little
embedded devices are not generally regarded as high-performing data movers. Provisioning 
with Gigabit Ethernet network cabling and low power SSD storage over USB3 go a long way 
towards alleviating those concerns. 

  Complaceny should be avoided here, it is easy to be deceived. These are still small devices
  and generally speaking, this design has a way of keeping most nodes fully tasked. Always 
  keep the basics in mind. It is critically important to give due consideration to key factors
  such as CPU resources, memory utilization, disk I/O, storage capacity, and network traffic; 
  it all adds up, and each impact the others. There is always a price to pay. 

Raw data gleaned from an Outpost event can be voluminous and detailed.

SentienlCam endeavors to always capture as much image detail as possible. As noted above 
in *Data Model* this requires much more space than a compressed video format. A high capture
rate provdes more data for analysis and modeling, reducing the likelyhood that key details 
might be missed. This also can allow for generating high quality full motion archival videos. 

Additionally, there can be multiple objects of interest moving through the field of view 
simultaneously. Collected logging data includes geometry, classification, and possibly 
labeling. This could represent the aggregated results inferred from one or more deep neural 
networks whether collected in real time by an Outpost node, or produced by the Sentinel. 
Or both.

It adds up in a hurry. *And the rest of the story...*

Much of it can be meaningless, trivial, forgettable, and simply not wanted. For example, 
imagine an outdoor camera with a view of both an entry into the home and the driveway. 

The occupants and their vehicles will pass in front of that camera multiple times per
day. Routine events such as these do not require a video record, or even a single image
be preserved. All the house needs to do, really, is take note that your car departed 
at 7:12 in the morning and arrived back home at 6:39 that evening. Happens every weekday.

All of those unexpected, unusual, exceptional events are not so disposable. Under certain 
circumstances, it might be desirable to produce a full archival video immediately. There
may be situations were such a record should be copied off-site as a precaution. Perhaps by 
policy, a full video record of evey package delivery is always kept for a period of time.

This all needs to be mostly automatic and self-maintaining. The end result should require the 
bare minimum of care and feeding. Ideally, set it up and forget about it. It should just work. 

*Saying it once more. Dream big*.

Video event playback and retention
----------------------------------

The ability to easily select and review historical events and then present them within a video
player is an obvious requirement. This will ultimately evolve into a set of services to search 
for, list, and replay events that have been cataloged. 

Librarian
---------

Begin to explore capitalizing on the functionality of the **librarian**  and its design philosophy 
as a vehicle to centralize knowledge and state.

Additional documentation
========================
- `Version History and Changelog <HISTORY.md>`_
- `Changes to imagenode project <docs/YingYangRanch_Changes.rst>`_
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
- imageZMQ
- imutils
- MessagePack
- NumPy
- pandas
- PyZMQ
- simplejpeg
  
Acknowledgements
================

- Dr. Adrian Rosebrock and the PyImageSearch team; his book: *Raspberry Pi for Computer Vision* 
  has been an invaluable resource.
- Jeff Bass (imagezmq, imagenode, and imagehub); his outstanding work has allowed this project
  to get off to a fast start.
