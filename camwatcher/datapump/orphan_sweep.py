"""orphan_sweep: reclaim data files that no longer belong to any event.

Standalone maintenance tool for SentinelCam data sinks. Run by hand, not on a
timer — deletion here is filesystem-driven rather than index-driven, and that is
exactly why it should be a deliberate act.

WHY THIS EXISTS

Every other deletion path in the system works forward from the event index: the
DailyCleanup task walks the index, decides which events have outlived their
value, and asks the datapump to remove them. Scene expiry works the same way,
just with a narrower scope. Both share a blind spot — an image file whose index
row is already gone is unreachable, because there is no event left to iterate.

Those files accumulate from partial deletions, interrupted writes, and index
files that were emptied or lost. On data1 the scene-retention catch-up sweep of
2026-09-02 reclaimed 86 GB and left ~10 GB behind in 40 date folders whose index
was missing or empty. Nothing in the normal retention machinery will ever touch
them.

WHAT COUNTS AS AN ORPHAN

Every stored filename begins with the event UUID it belongs to:

    images/{date}/{EventID}_{Timestamp}.jpg
    crops/{date}/{EventID}_{ObjID}_{Seqnum}_{Class}_{Phase}.jpg
    camwatcher/{date}/{EventID}_{type}.csv

So the test is whether that leading UUID still appears in ANY date's
`camwatcher.csv` index — deliberately not just the folder's own index. Storage is
date-partitioned by RECEIVE time while an event is indexed by its START time, so
a file routinely lives in a different day-folder than its event's index row: an
event straddling midnight, or a track that goes quiescent and resumes under its
earlier event id. Those files are perfectly good data. Judging them against their
own folder's index alone would have deleted 49,214 of them on data1 — the tail of
every midnight-crossing event on the sink. A file whose event is indexed anywhere
is never touched here, whatever its age; retention policy is not this tool's
business.

SAFETY

- Reports by default. Deleting requires --delete.
- Recent dates are skipped (--min-age-days, default 2). An event's frames can
  reach disk fractionally before its index row is written, and a sweep must
  never race live capture.
- A date whose index is missing or empty is NOT swept by default. Every file on
  such a date looks orphaned, which is equally consistent with "the index was
  lost" as with "the data is garbage" — so it is reported separately and needs
  --unindexed-dates to act on.
- Files that do not parse as {EventID}_... are reported and never deleted.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import re
import sys
import csv
import time
import argparse
import logging
from datetime import date as _date, datetime, timedelta

import yaml

logger = logging.getLogger(__name__)

# Event identifiers are 32-character hex UUIDs. A filename that does not start
# with one is not something this tool understands, and is therefore not
# something it is willing to delete.
EVENTID_RE = re.compile(r'^([0-9a-f]{32})_')

# The index file itself, which lives alongside the per-event detail CSVs.
INDEX_FILE = 'camwatcher.csv'

# Event UUID is the 4th column of the index (node, viewname, timestamp, event, ...)
IDX_EVENT_COL = 3

# Pace deletion the way the camwatcher purge thread does: yield briefly every
# so many files so a large sweep stays background work.
YIELD_FILES = 500
YIELD_SECS = 0.05


def read_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def date_folders(root):
    """Sorted YYYY-MM-DD folder names under a plane's root directory."""
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)) and len(d) == 10 and d[4] == '-')


def build_index(csvdir):
    """Every event UUID the sink knows, plus each date's own membership.

    Returns (all_events, by_date) where by_date maps a date to its event set, or
    to None when that date has no readable index. The global set is what decides
    orphanhood; the per-date sets only separate "belongs to another date" from
    "belongs to nothing", which is diagnostic rather than actionable.
    """
    all_events, by_date = set(), {}
    for day in date_folders(csvdir):
        events = indexed_events(csvdir, day)
        by_date[day] = events
        if events:
            all_events |= events
    return all_events, by_date


def indexed_events(csvdir, day):
    """Event UUIDs named by a date's index. Returns None when there is no index.

    None and empty set are meaningfully different: no index file at all (or an
    empty one) means the date's membership is unknown, not that nothing belongs
    to it. Callers must treat that case as unindexed rather than all-orphaned.
    """
    path = os.path.join(csvdir, day, INDEX_FILE)
    if not os.path.isfile(path):
        return None
    events = set()
    try:
        with open(path, newline='') as f:
            for row in csv.reader(f):
                if len(row) > IDX_EVENT_COL:
                    events.add(row[IDX_EVENT_COL].strip())
    except Exception:
        logger.exception(f"Unreadable index for {day}; treating date as unindexed")
        return None
    return events or None


def scan_plane(root, day, known_all, known_here, skip=()):
    """Classify one date folder of one plane.

    A file is an orphan only when its event is indexed NOWHERE. One indexed on a
    different date is counted as cross-date and left strictly alone.

    Returns (orphans, orphan_bytes, cross_date, unparsed).
    """
    folder = os.path.join(root, day)
    orphans, orphan_bytes, unparsed, cross_date = [], 0, [], 0
    try:
        # scandir over listdir: these folders hold tens of thousands of files and
        # the sweep walks every date, so the cached dirent type is worth having —
        # only an actual orphan costs a stat.
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name in skip or not entry.is_file():
                    continue
                m = EVENTID_RE.match(entry.name)
                if not m:
                    unparsed.append(entry.path)
                    continue
                if m.group(1) in known_here:
                    continue
                if m.group(1) in known_all:
                    cross_date += 1   # filed by receive date, indexed by start date
                    continue
                orphans.append(entry.path)
                try:
                    orphan_bytes += entry.stat().st_size
                except OSError:
                    pass
    except FileNotFoundError:
        pass
    return orphans, orphan_bytes, cross_date, unparsed


def remove(paths):
    """Unlink a list of files, yielding as the work is done. Returns (count, bytes)."""
    removed, freed = 0, 0
    for i, path in enumerate(paths, start=1):
        try:
            size = os.path.getsize(path)
            os.unlink(path)
            removed += 1
            freed += size
        except FileNotFoundError:
            pass  # already gone; sweeping is idempotent
        except OSError as e:
            logger.warning(f"Could not remove {path}: {e}")
        if i % YIELD_FILES == 0:
            time.sleep(YIELD_SECS)
    return removed, freed


def gb(n):
    return n / (1024 ** 3)


def sweep(cfg, args):
    csvdir = cfg['datafolder']
    planes = [('images', cfg['imagefolder'], ()),
              ('crops', cfg.get('cropfolder'), ()),
              ('data', csvdir, (INDEX_FILE,))]
    planes = [(n, r, s) for (n, r, s) in planes if r]

    cutoff = _date.today() - timedelta(days=args.min_age_days)
    days = sorted({d for _, root, _ in planes for d in date_folders(root)})
    if args.date:
        days = [d for d in days if d == args.date]

    totals = {'orphans': 0, 'bytes': 0, 'unindexed_files': 0, 'unindexed_bytes': 0,
              'unparsed': 0, 'removed': 0, 'freed': 0, 'dates': 0, 'unindexed_dates': 0,
              'cross_date': 0}

    known_all, by_date = build_index(csvdir)
    logger.info(f"index holds {len(known_all)} events across {len(by_date)} dates")

    for day in days:
        try:
            if datetime.strptime(day, '%Y-%m-%d').date() > cutoff:
                continue  # too recent to distinguish an orphan from a live event
        except ValueError:
            continue

        known_here = by_date.get(day)
        unindexed = known_here is None
        if unindexed:
            known_here = set()

        found, found_bytes, unparsed = [], 0, []
        for _, root, skip in planes:
            o, b, x, u = scan_plane(root, day, known_all, known_here, skip)
            found.extend(o)
            found_bytes += b
            totals['cross_date'] += x
            unparsed.extend(u)

        if unparsed:
            totals['unparsed'] += len(unparsed)
            logger.warning(f"{day}: {len(unparsed)} unrecognized filename(s), left alone "
                           f"(e.g. {os.path.basename(unparsed[0])})")
        if not found:
            continue

        if unindexed:
            totals['unindexed_dates'] += 1
            totals['unindexed_files'] += len(found)
            totals['unindexed_bytes'] += found_bytes
            if not args.unindexed_dates:
                logger.info(f"{day}: NO INDEX — {len(found)} files, {gb(found_bytes):.2f} GB "
                            f"(skipped; --unindexed-dates to include)")
                continue
        else:
            totals['dates'] += 1
            totals['orphans'] += len(found)
            totals['bytes'] += found_bytes

        label = 'NO INDEX' if unindexed else 'orphaned'
        if args.delete:
            removed, freed = remove(found)
            totals['removed'] += removed
            totals['freed'] += freed
            logger.info(f"{day}: {label} — removed {removed} files, {gb(freed):.2f} GB")
        else:
            logger.info(f"{day}: {label} — {len(found)} files, {gb(found_bytes):.2f} GB")

    logger.info("-" * 62)
    if totals['cross_date']:
        logger.info(f"cross-date files         : {totals['cross_date']} "
                    f"(indexed on another date — left alone)")
    logger.info(f"orphans on indexed dates : {totals['orphans']} files, "
                f"{gb(totals['bytes']):.2f} GB across {totals['dates']} dates")
    logger.info(f"files on unindexed dates : {totals['unindexed_files']} files, "
                f"{gb(totals['unindexed_bytes']):.2f} GB across "
                f"{totals['unindexed_dates']} dates"
                f"{'' if args.unindexed_dates else '  [not swept]'}")
    if totals['unparsed']:
        logger.info(f"unrecognized filenames   : {totals['unparsed']} (never deleted)")
    if args.delete:
        logger.info(f"REMOVED                  : {totals['removed']} files, "
                    f"{gb(totals['freed']):.2f} GB reclaimed")
    else:
        logger.info("report only — pass --delete to reclaim")


def main():
    parser = argparse.ArgumentParser(
        description="Find (and optionally delete) data files with no event index row.")
    parser.add_argument('--delete', action='store_true',
                        help='actually remove the orphans (default: report only)')
    parser.add_argument('--date', metavar='YYYY-MM-DD',
                        help='restrict the sweep to a single date')
    parser.add_argument('--min-age-days', type=int, default=2, metavar='N',
                        help='skip dates newer than N days, so the sweep cannot race '
                             'live capture (default: 2)')
    parser.add_argument('--unindexed-dates', action='store_true',
                        help='also sweep dates whose index is missing or empty — every '
                             'file there looks orphaned, so this is opt-in')
    parser.add_argument('--config', default=os.path.join(os.path.expanduser("~"), "datapump.yaml"),
                        help='datapump configuration to read the data paths from')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout)])

    if not os.path.isfile(args.config):
        print(f"Configuration file not found: {args.config}", file=sys.stderr)
        sys.exit(1)
    cfg = read_config(args.config)

    logger.info(f"Orphan sweep starting ({'DELETE' if args.delete else 'report only'}), "
                f"skipping dates newer than {args.min_age_days} days")
    try:
        sweep(cfg, args)
    except Exception:
        logger.exception("Orphan sweep failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
