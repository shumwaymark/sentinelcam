import argparse
from datetime import datetime
from sentinelcam.datafeed import DataFeed

ap = argparse.ArgumentParser()
ap.add_argument("-d", "--date", default=datetime.utcnow().isoformat()[:10], help="Date (YYYY-MM-DD)")
ap.add_argument("-d2", "--date2", help="End date to define range (YYYY-MM-DD)")
ap.add_argument("-c", "--count", default=12, help="Number of days to show")
args = vars(ap.parse_args())

feed = DataFeed('tcp://data1:5556')
date_count = int(args["count"])
all_dates = feed.get_date_list()
if args['date2']:
    all_dates.reverse()
    datelist = [d for d in all_dates if d >= args['date'] and d <= args['date2']]
else:
    datelist = all_dates

for day in datelist[:date_count]:
    cindx = feed.get_date_index(day)
    types = cindx['type'].value_counts()
    trks = types['trk'] if 'trk' in types else 0
    objs = types['obj'] if 'obj' in types else 0
    faces = types['fd1'] if 'fd1' in types else 0
    recon = types['fr1'] if 'fr1' in types else 0
    vspeed = types['vsp'] if 'vsp' in types else 0
    print(day,trks,objs,faces,recon,vspeed)
