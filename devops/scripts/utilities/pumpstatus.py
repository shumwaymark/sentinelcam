import argparse
from datetime import date
from sentinelcam.datafeed import DataFeed

cfg = {'datapump': 'tcp://data1:5556'}

ap = argparse.ArgumentParser()
ap.add_argument("-d", "--date", default=str(date.today()), help="Date as YYYY-MM-DD")
ap.add_argument("-c", "--count", default=12, help="Number of events")
args = vars(ap.parse_args())

event_date = args["date"]
event_count = int(args["count"])

feed = DataFeed(cfg["datapump"])
cwIndx = feed.get_date_index(event_date)
c=1
for trk in cwIndx.loc[cwIndx['type'] == 'trk'].itertuples():
    images = []
    trk_cnt, obj_cnt, fd1_cnt, fr1_cnt, vsp_cnt = 0,0,0,0,0
    try:
        images = feed.get_image_list(event_date, trk.event)
        refcnts = {ref: len(feed.get_tracking_data(event_date, trk.event, ref).index)
            for ref in cwIndx.loc[cwIndx['event'] == trk.event]['type'].to_list()}
        trk_cnt = refcnts.get('trk',0)
        obj_cnt = refcnts.get('obj',0)
        fd1_cnt = refcnts.get('fd1',0)
        fr1_cnt = refcnts.get('fr1',0)
        vsp_cnt = refcnts.get('vsp',0)
    except DataFeed.ImageSetEmpty as e:
        print(f"No image data for {e.date},{e.evt}")
    except DataFeed.TrackingSetEmpty as e:
        #print(f"No tracking data for {e.date},{e.evt},{e.trk}")
        pass
    except Exception as e:
        print(f"Data retrieval failure for {event_date},{trk.event}: {str(e)}")
    frames = len(images)
    capBeg, capEnd, capLen, fps = None,None,0,0
    if frames > 0:
        capBeg = images[0]
        if frames > 1:
            capEnd = images[-1]
            capLen = (capEnd-capBeg).seconds
            if capLen > 0:
                fps = frames / capLen
    outpost = f"{trk.node} / {trk.viewname}"
    reflist = str((trk_cnt,obj_cnt,fd1_cnt,fr1_cnt,vsp_cnt))
    print(f"{outpost:20} {str(capBeg)[11:23]} {capLen:3} {frames:4} {fps:>4.1f} {reflist:25} {trk.event}")
    c+=1
    if c > event_count:
        break
