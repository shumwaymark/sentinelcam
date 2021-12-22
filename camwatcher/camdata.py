"""camdata: access to camera event data from sentinelcam outpost nodes

Copyright (c) 2021 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import pandas as pd
from datetime import datetime

class CamData:
    """ Data and access methods to camwatcher CSV and image data.

    Always operates within the context of a specific date. Any
    call to `set_event()` should refernce an event within the 
    current date index. Use `set_date()` as necessary to change
    context to another date. 

    Parameters
    ----------
    csvdir : str  
        High-level folder name for camwatcher CSV date folders
    imgdir : str  
        High-level folder name for camwatcher image folders
    date : str, optional
        Target date in "YYYY-MM-DD" format. This defaults to 
        current date if not specified.
    
    Methods
    -------
    set_date(date() -> None
        Set index to specified (YYYY-MM-DD) date
    get_date() -> str
        Return current index date as YYYY-MM-DD
    get_date_list() -> List
        Returns list of available YYYY-MM-DD date folders from most recent to oldest
    get_index_name(date) -> str
        For given date, returns filesystem pathname to camwatcher index
    get_index() -> pandas.DataFrame
        Returns reference to the current camwatcher index data
    get_last_event() -> str
        Returns most recent event id 
    set_event(event) -> None
        Set index to specifed event id
    get_event_node() -> str
        Returns node name associated with current event
    get_event_view() -> str
        Returns view name associated with current event
    get_event_types() -> pandas.DataFrame
        Returns the event types available for this event
    get_event_pathname(event, type) -> str
        Retruns filesystem pathname to event detail file
    get_event_data(type) -> pandas.DataFrame
        Returns reference to tracking detail for current event and type
    get_event_images() -> List
        Returns list of pathnames to individual image frame files in chronological order
    """

    IDXFILE = "camwatcher.csv"
    IDXCOLS = ["node","viewname","timestamp","event","fps","type"]
    IDXTYPES = ["trk"]

    def set_date(self, date):
        """ Initialize CamData objet to the given date 

        Loads the camwatcher index data for the given date. Clears any existing
        references to event and camera data. This is a clean reset to an entirely
        new date, or used as a refresh of the current date.   

        Parameters
        ----------
        date : str
            Target date in "YYYY-MM-DD" format
        """

        self._ymd = date
        self._indexfile = self.get_index_name(date)
        self._lastEvent = None
        self._event_id = None
        self._event_node = None
        self._event_view = None
        self._event_start = None
        self._event_subset = None
        self._event_types = None
        self._event_data = None
        if self._indexfile:
            # read camwatcher index into pandas DataFrame
            self._index = pd.read_csv(
                            self._indexfile, 
                            names=CamData.IDXCOLS, 
                            parse_dates=["timestamp"]
                          ).sort_values(
                             by="timestamp", ascending=False)
            # retrive the event_id for the most recent event in the index
            self._lastEvent = self._index.iloc[0].event
        else:
            # if no index file, provide an empty DataFrame 
            self._index = pd.DataFrame(columns=CamData.IDXCOLS)

    def get_date(self):
        """ Return current index date
        
        Returns
        -------
        str
            Currently selected index date in YYYY-MM-DD format.
        """

        return self._ymd

    def get_index_name(self, date):
        """ Return filename reference to camwatcher index

        Based on the supplied date parameter, return the pathname to 
        the camwatcher index if it exsits. Otherwise return None. This 
        function can be used to quickly determine whether any data is 
        available for the specified date. 

        Parameters
        ----------
        date : str
            Target date in "YYYY-MM-DD" format
        
        Returns
        -------
        str
            Pathname to camwatcher index for supplied date, or None 
        """

        indexname = os.path.join(self._index_path, date, CamData.IDXFILE)
        if not os.path.exists(indexname):
            indexname = None
        return indexname

    def get_index(self):
        """ Return reference to the current camwatcher index as a pandas DataFrame 
        
        Returns
        -------
        pandas.DataFrame
            Reference to camwatcher index as a pandas.DataFrame
        """

        return self._index

    def get_last_event(self):
        """ Return most recent Event ID 
        
        Returns
        -------
        str
            Most recent event id within the index
        """

        return self._lastEvent

    def set_event(self, event):
        """ Set camwatcher index to the specified Event ID

        Sets index to the specified event. Establishes references to event detail 
        files. Loads camera detail data for default event type. A prerequisite
        to establishing valid references to event and camera data. 

        Parameters
        ----------
        event : str
            Event ID
        """

        self._event_id = event
        self._event_subset = self._index[(self._index['event'] == event)]
        self._event_node = self._event_subset.iloc[0].node
        self._event_view = self._event_subset.iloc[0].viewname
        self._event_start = self._event_subset["timestamp"].min()
        self._event_types = self._event_subset["type"]

    def get_event_node(self):
        """ Return node name from event
            
        Must have first invoked `set_event()` to load camera detail for the event.
        
        Returns
        -------
        str
            Node name associated with the current event
        """

        return self._event_node

    def get_event_view(self):
        """ Return view name from the event
            
        Must have first invoked `set_event()` to load camera detail for the event.
        
        Returns
        -------
        str
            View name associated with the current event
        """

        return self._event_view

    def get_event_start(self):
        """ Return view name from the event
            
        Must have first invoked `set_event()` to load camera detail for the event.
        
        Returns
        -------
        datetime
            Starting timestamp for current event
        """

        return self._event_start

    def get_event_types(self):
        """ Return the event types for the selected event. 
        
        Must have first invoked `set_event()` to load camera detail for the event.
        
        Returns
        -------
        pandas.DataFrame
            The available event types for the current event.
        """
        
        return self._event_types
    
    def get_event_pathname(self, event, type='trk'):
        """ Return pathname to the camera event detail file
                
        Parameters
        ----------
        event : str
            Event ID
        type : str, optional
            Event type
        
        Returns
        -------
        str
            Pathname to camwatcher detail for specified event and type
        """

        return os.path.join(self._index_path, self._ymd, event + "_" + type + ".csv")

    def get_event_data(self, type='trk'):
        """ Return reference to selected event tracking data as a pandas DataFrame 
        
        Must have first invoked `set_event()` to load camera detail for the event.
                
        Parameters
        ----------
        type : str, optional
            Event type 
        
        Returns
        -------
        pandas.DataFrame
            Reference to event detail data as a pandas.DataFrame
        """

        self._event_data = pd.read_csv(
            self.get_event_pathname(self._event_id, type),
            parse_dates=['timestamp']
        )
        self._event_data["elapsed"] = self._event_data["timestamp"] - self._event_start
        return self._event_data

    def get_date_list(self):
        """ Returns list of available YYYY-MM-DD date folders from most recent to oldest
        
        Returns
        -------
        List
            The list of available date folders
        """
        return sorted([d[-10:] for d in list(self._list_date_folders())], reverse=True)
            
    def get_event_images(self):
        """ Returns list of pathnames to individual image frame files in chronological order
        
        Returns
        -------
        List
            The list of pathnames to individual image frame files
        """
        return sorted(list(self._list_event_images()))

    def _list_date_folders(self):
        # returns a list of the available date folders
        return self._list_files(self._index_path, prefix=None)

    def _list_event_images(self):
        # return the set of image files for the current event
        imagefolder = os.path.join(self._image_path, self._ymd)
        return self._list_files(imagefolder, prefix=self._event_id)

    def _list_files(self, basePath, prefix):
        # loop over filenames in the directory
        for filename in os.listdir(basePath):
            # skip any files without a matching filename prefix
            if prefix is not None and not filename.startswith(prefix):
                continue
            # construct the path to the image and yield it
            imagePath = os.path.join(basePath, filename)
            yield imagePath

    def __init__(self, csvdir, imgdir, date = datetime.utcnow().isoformat()[:10]):
        self._index_path = csvdir
        self._image_path = imgdir
        self.set_date(date)

# ----------------------------------------------------------------------------------------
#   See below for usaage 
# ----------------------------------------------------------------------------------------

cfg = {'csvdir': '/mnt/usb1/imagedata/camwatcher',
       'imgdir': '/mnt/usb1/imagedata/video'} 

if __name__ == '__main__' :

    cdata = CamData(cfg["csvdir"], cfg["imgdir"])  # allocate and initialize index (current date)
    cindx = cdata.get_index()                      # get reference to index DataFrame
    
    # most recent 5 events
    for row in cindx[:5].itertuples():
        print(row.node + " " + row.viewname + " " + str(row.timestamp) + " " + row.event)

    event_id = cdata.get_last_event()      # grab the most recent event id
    if event_id:
        cdata.set_event(event_id)          # load event data 
        evt_data = cdata.get_event_data()  # cam tracking dataset from event 
    
        print(f"Event ID [{event_id}] started at {str(cdata.get_event_start())}")
        for row in evt_data[:10].itertuples():
            print(str(row.timestamp) + " " + 
                  str(row.elapsed) + " " + 
                  str(row.objid) + " " + 
                  str(row.centroid_x) + " " + 
                  str(row.centroid_y))
