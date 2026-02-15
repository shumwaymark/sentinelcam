#!/usr/bin/env python
"""
Find events with missing tracking data for mass_update catch-up.
Outputs "date eventid" format suitable for EventList input.

Examples:
  # Events with obj but missing fd1
  python find_missing_data.py --has obj --missing fd1

  # Events with fd1 but missing fr1
  python find_missing_data.py -d 2026-01-07 --has fd1 --missing fr1

  # Events with trk and obj but missing vsp
  python find_missing_data.py --has trk,obj --missing vsp --limit 50
"""
import argparse
from datetime import date
from sentinelcam.datafeed import DataFeed

cfg = {'datapump': 'tcp://data1:5556'}

ap = argparse.ArgumentParser(description='Find events with missing tracking data')
ap.add_argument("-d", "--date", default=str(date.today()),
                help="Date as YYYY-MM-DD (default: today)")
ap.add_argument("--has", required=True,
                help="Comma-separated data types that must exist (e.g., obj or trk,obj)")
ap.add_argument("--missing", required=True,
                help="Comma-separated data types that must be missing (e.g., fd1 or fd1,fr1)")
ap.add_argument("--outpost", required=True,
                help="optionally restrict to outpost node name e.g., OP01")
ap.add_argument("-l", "--limit", type=int, default=None,
                help="Maximum number of events to return (default: all)")
ap.add_argument("-v", "--verbose", action="store_true",
                help="Show detailed info about each event")
args = ap.parse_args()

event_date = args.date
outpost = args.outpost
has_types = [t.strip() for t in args.has.split(',')]
missing_types = [t.strip() for t in args.missing.split(',')]

feed = DataFeed(cfg["datapump"])
cwIndx = feed.get_date_index(event_date)

count = 0
for trk in cwIndx.loc[cwIndx['type'] == 'trk'].itertuples():
    # Get reference counts for this event
    try:
        if args.outpost and trk.node != outpost:
            continue
        event_refs = cwIndx.loc[cwIndx['event'] == trk.event]['type'].to_list()
        refcnts = {}
        for ref in event_refs:
            try:
                refcnts[ref] = len(feed.get_tracking_data(event_date, trk.event, ref).index)
            except DataFeed.TrackingSetEmpty:
                refcnts[ref] = 0

        # Check if event has all required types
        has_all = all(refcnts.get(t, 0) > 0 for t in has_types)

        # Check if event is missing all specified types
        missing_all = all(refcnts.get(t, 0) == 0 for t in missing_types)

        if has_all and missing_all:
            if args.verbose:
                counts = {k: v for k, v in refcnts.items() if v > 0}
                print(f"# {trk.node}/{trk.viewname} {trk.timestamp} {counts}")
            print(f"{event_date} {trk.event}")
            count += 1
            if args.limit and count >= args.limit:
                break

    except DataFeed.ImageSetEmpty:
        # Skip events with no images
        pass
    except Exception as e:
        if args.verbose:
            print(f"# Error checking {event_date},{trk.event}: {str(e)}")

if args.verbose:
    print(f"# Found {count} events matching criteria")
