CREATE TABLE cam_event (
    node_name    VARCHAR(64),  -- Imagenode name
    view_name    VARCHAR(64),  -- camera view name
    start_time   TIMESTAMP,    -- event start time
    pipe_event	 INTEGER,      -- pipeline event id at start
    pipe_fps	 SMALLINT      -- pipeline velocity at start
);

CREATE TABLE cam_tracking (
    node_name    VARCHAR(64),  -- Imagenode name
    view_name    VARCHAR(64),  -- camera view name
    start_time   TIMESTAMP,    -- initial start time for event
    pipe_event   INTEGER,      -- can vary to support multiple events
    object_time  TIMESTAMP,    -- when motion was detected
    object_tag   INTEGER,      -- object identifier
    centroid_x   INTEGER,      -- object centroid X-coord
    centroid_y   INTEGER       -- object centroid Y-coord
);
