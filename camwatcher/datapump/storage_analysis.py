"""storage_analysis: Storage visibility reporting for SentinelCam data sinks.

Standalone batch script that walks the sentinelcam filesystem, builds a
summary report of disk capacity, daily intake, and per-view breakdowns.
Produces a pre-computed pickle file served on demand by DataPump.

Designed to run nightly via systemd timer after DailyCleanup has completed.
Does not delete anything — this is a reporting and measurement tool only.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import sys
import pickle
import yaml
import logging
from datetime import datetime, date, timedelta

import pandas

# Report format version — increment when structure changes
REPORT_VERSION = 1

# Camwatcher index columns (no header row in CSV)
IDXCOLS = ["node", "viewname", "timestamp", "event", "width", "height", "type"]

logger = logging.getLogger(__name__)


def readConfig(configFile):
    """Read YAML configuration file."""
    with open(configFile) as f:
        return yaml.safe_load(f)


def get_disk_metrics(disk_path):
    """Collect disk capacity metrics via os.statvfs.

    Parameters
    ----------
    disk_path : str
        Filesystem path or mount point to measure

    Returns
    -------
    dict
        Keys: total_bytes, used_bytes, free_bytes
    """
    st = os.statvfs(disk_path)
    total = st.f_frsize * st.f_blocks
    free = st.f_frsize * st.f_bavail  # available to non-root
    used = total - free
    return {
        'total_bytes': total,
        'used_bytes': used,
        'free_bytes': free,
    }


def list_date_folders(base_path):
    """Yield YYYY-MM-DD folder names that exist under base_path.

    Parameters
    ----------
    base_path : str
        Parent directory containing date-named subdirectories

    Yields
    ------
    str
        Date folder names in YYYY-MM-DD format
    """
    if not os.path.isdir(base_path):
        return
    with os.scandir(base_path) as entries:
        for entry in entries:
            if entry.is_dir() and len(entry.name) == 10:
                # Basic validation: looks like YYYY-MM-DD
                try:
                    datetime.strptime(entry.name, "%Y-%m-%d")
                    yield entry.name
                except ValueError:
                    continue


def load_event_index(csv_path, ymd):
    """Load the camwatcher.csv event index for a given date.

    Returns a DataFrame mapping event UUIDs to (node, viewname).
    Returns empty DataFrame if the index file is missing or empty.

    Parameters
    ----------
    csv_path : str
        Root camwatcher CSV directory
    ymd : str
        Date string in YYYY-MM-DD format

    Returns
    -------
    pandas.DataFrame
        Event index with columns: node, viewname, event
    """
    index_file = os.path.join(csv_path, ymd, "camwatcher.csv")
    if not os.path.isfile(index_file):
        return pandas.DataFrame(columns=["node", "viewname", "event"])
    try:
        df = pandas.read_csv(index_file, names=IDXCOLS)
        # Deduplicate: one row per event with its node/viewname
        return df[["node", "viewname", "event"]].drop_duplicates(subset=["event"])
    except (pandas.errors.EmptyDataError, pandas.errors.ParserError):
        return pandas.DataFrame(columns=["node", "viewname", "event"])


def scan_date_folder(csv_path, img_path, ymd):
    """Scan a single date folder and produce per-view storage metrics.

    Parameters
    ----------
    csv_path : str
        Root camwatcher CSV directory
    img_path : str
        Root images directory
    ymd : str
        Date string in YYYY-MM-DD format

    Returns
    -------
    list of dict
        One dict per (node, viewname) with storage metrics
    """
    event_index = load_event_index(csv_path, ymd)

    # Build event-to-view lookup
    event_to_view = {}
    for row in event_index.itertuples(index=False):
        event_to_view[row.event] = (row.node, row.viewname)

    # Accumulator: (node, viewname) -> {image_count, image_bytes, csv_count, csv_bytes, event_ids}
    view_stats = {}

    def ensure_view(node, viewname):
        key = (node, viewname)
        if key not in view_stats:
            view_stats[key] = {
                'image_count': 0, 'image_bytes': 0,
                'csv_count': 0, 'csv_bytes': 0,
                'event_ids': set()
            }
        return view_stats[key]

    # Scan CSV files in camwatcher/YYYY-MM-DD/
    csv_date_dir = os.path.join(csv_path, ymd)
    if os.path.isdir(csv_date_dir):
        with os.scandir(csv_date_dir) as entries:
            for entry in entries:
                if not entry.is_file() or not entry.name.endswith('.csv'):
                    continue
                try:
                    fsize = entry.stat().st_size
                except OSError:
                    continue

                if entry.name == "camwatcher.csv":
                    # Index file — attribute to all views proportionally?
                    # No: count once under a synthetic key, or distribute.
                    # Simplest: attribute to all views equally is wrong.
                    # Just count it in the overall CSV tally for each view
                    # that has events on this date. Store as overhead.
                    # Actually, just skip attribution — it's one file per date.
                    # We'll count it but not attribute to a specific view.
                    continue
                else:
                    # Tracking detail CSV: {EventID}_{type}.csv
                    parts = entry.name.rsplit('_', 1)
                    if len(parts) == 2:
                        event_id = parts[0]
                        if event_id in event_to_view:
                            node, viewname = event_to_view[event_id]
                            stats = ensure_view(node, viewname)
                            stats['csv_count'] += 1
                            stats['csv_bytes'] += fsize
                            stats['event_ids'].add(event_id)

    # Scan image files in images/YYYY-MM-DD/
    img_date_dir = os.path.join(img_path, ymd)
    if os.path.isdir(img_date_dir):
        with os.scandir(img_date_dir) as entries:
            for entry in entries:
                if not entry.is_file() or not entry.name.endswith('.jpg'):
                    continue
                try:
                    fsize = entry.stat().st_size
                except OSError:
                    continue

                # Image filename: {EventID}_{Timestamp}.jpg
                underscore_pos = entry.name.find('_')
                if underscore_pos > 0:
                    event_id = entry.name[:underscore_pos]
                    if event_id in event_to_view:
                        node, viewname = event_to_view[event_id]
                        stats = ensure_view(node, viewname)
                        stats['image_count'] += 1
                        stats['image_bytes'] += fsize
                        stats['event_ids'].add(event_id)

    # Convert to list of dicts for DataFrame construction
    rows = []
    for (node, viewname), stats in view_stats.items():
        rows.append({
            'date': ymd,
            'node': node,
            'viewname': viewname,
            'event_count': len(stats['event_ids']),
            'image_count': stats['image_count'],
            'image_bytes': stats['image_bytes'],
            'csv_count': stats['csv_count'],
            'csv_bytes': stats['csv_bytes'],
        })

    return rows


def get_folder_mtime(path):
    """Get the most recent modification time of a directory.

    Parameters
    ----------
    path : str
        Directory path

    Returns
    -------
    float
        Modification timestamp, or 0.0 if path doesn't exist
    """
    if os.path.isdir(path):
        try:
            return os.stat(path).st_mtime
        except OSError:
            return 0.0
    return 0.0


def load_existing_report(report_file):
    """Load an existing report pickle, or return None if unavailable.

    Parameters
    ----------
    report_file : str
        Path to the storage_report.pickle file

    Returns
    -------
    dict or None
        The report dict, or None if not found / incompatible
    """
    if not os.path.isfile(report_file):
        return None
    try:
        with open(report_file, 'rb') as f:
            report = pickle.load(f)
        if not isinstance(report, dict) or 'report_version' not in report:
            logger.warning("Existing report missing version field — rebuilding from scratch")
            return None
        if report['report_version'] != REPORT_VERSION:
            logger.warning(f"Report version mismatch (found {report['report_version']}, "
                           f"expected {REPORT_VERSION}) — rebuilding from scratch")
            return None
        return report
    except Exception as e:
        logger.warning(f"Failed to load existing report: {e} — rebuilding from scratch")
        return None


def save_report(report, report_file):
    """Atomically write the report pickle file.

    Writes to a temp file first, then renames to avoid partial writes
    if the process is interrupted.

    Parameters
    ----------
    report : dict
        The report dictionary
    report_file : str
        Destination path
    """
    tmp_file = report_file + '.tmp'
    with open(tmp_file, 'wb') as f:
        pickle.dump(report, f)
    os.replace(tmp_file, report_file)


def run_analysis(cfg):
    """Main analysis logic.

    Parameters
    ----------
    cfg : dict
        Configuration from storage_analysis.yaml
    """
    datasink_name = cfg['datasink_name']
    sentinelcam_root = os.path.expanduser(cfg['sentinelcam_root'])
    report_dir = os.path.expanduser(cfg['report_path'])
    disk_path = cfg['disk_path']
    history_days = cfg.get('report_history_days', 365)

    csv_path = os.path.join(sentinelcam_root, 'camwatcher')
    img_path = os.path.join(sentinelcam_root, 'images')
    report_file = os.path.join(report_dir, 'storage_report.pickle')

    # Ensure report directory exists
    os.makedirs(report_dir, exist_ok=True)

    # Load existing report if available
    existing = load_existing_report(report_file)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    if existing is None:
        # Full scan — build from scratch
        logger.info("No existing report found — performing full filesystem scan")
        all_dates = set()
        for folder in list_date_folders(csv_path):
            all_dates.add(folder)
        for folder in list_date_folders(img_path):
            all_dates.add(folder)

        daily_rows = []
        for ymd in sorted(all_dates):
            rows = scan_date_folder(csv_path, img_path, ymd)
            daily_rows.extend(rows)
            logger.debug(f"Scanned {ymd}: {len(rows)} view entries")

        daily_summary = pandas.DataFrame(daily_rows)
        disk_summary = pandas.DataFrame()

    else:
        # Incremental scan
        logger.info("Incremental update from existing report")
        daily_summary = existing['daily_summary'].copy()
        disk_summary = existing['disk_summary'].copy()
        last_scan = existing.get('generated_at', datetime.min)
        if isinstance(last_scan, datetime):
            last_scan_ts = last_scan.timestamp()
        else:
            last_scan_ts = 0.0

        # Determine which dates need rescanning
        rescan_dates = set()

        # Always rescan today and yesterday
        rescan_dates.add(today)
        rescan_dates.add(yesterday)

        # Rescan any date folder modified since last scan
        for folder in list_date_folders(csv_path):
            csv_dir = os.path.join(csv_path, folder)
            if get_folder_mtime(csv_dir) > last_scan_ts:
                rescan_dates.add(folder)
        for folder in list_date_folders(img_path):
            img_dir = os.path.join(img_path, folder)
            if get_folder_mtime(img_dir) > last_scan_ts:
                rescan_dates.add(folder)

        logger.info(f"Rescanning {len(rescan_dates)} date(s): "
                     f"{', '.join(sorted(rescan_dates)[:5])}{'...' if len(rescan_dates) > 5 else ''}")

        # Remove old rows for rescanned dates, then append fresh data
        if len(daily_summary) > 0 and len(rescan_dates) > 0:
            daily_summary = daily_summary[~daily_summary['date'].isin(rescan_dates)]

        new_rows = []
        for ymd in sorted(rescan_dates):
            rows = scan_date_folder(csv_path, img_path, ymd)
            new_rows.extend(rows)
            logger.debug(f"Rescanned {ymd}: {len(rows)} view entries")

        if new_rows:
            new_df = pandas.DataFrame(new_rows)
            daily_summary = pandas.concat([daily_summary, new_df], ignore_index=True)

    # Collect disk metrics
    disk_metrics = get_disk_metrics(disk_path)
    now = datetime.utcnow()

    # Compute sentinelcam tree size from daily_summary
    if len(daily_summary) > 0:
        total_image_bytes = int(daily_summary['image_bytes'].sum())
        total_csv_bytes = int(daily_summary['csv_bytes'].sum())
    else:
        total_image_bytes = 0
        total_csv_bytes = 0

    disk_row = pandas.DataFrame([{
        'scan_date': now,
        'total_bytes': disk_metrics['total_bytes'],
        'used_bytes': disk_metrics['used_bytes'],
        'free_bytes': disk_metrics['free_bytes'],
        'sentinelcam_bytes': total_image_bytes + total_csv_bytes,
        'image_bytes': total_image_bytes,
        'csv_bytes': total_csv_bytes,
    }])
    disk_summary = pandas.concat([disk_summary, disk_row], ignore_index=True)

    # Trim old data beyond retention window
    cutoff = (date.today() - timedelta(days=history_days)).isoformat()
    if len(daily_summary) > 0:
        daily_summary = daily_summary[daily_summary['date'] >= cutoff]
    if len(disk_summary) > 0:
        disk_summary = disk_summary[disk_summary['scan_date'] >= cutoff]

    # Sort for clean output
    if len(daily_summary) > 0:
        daily_summary = daily_summary.sort_values(
            by=['date', 'node', 'viewname']).reset_index(drop=True)

    # Build report
    report = {
        'report_version': REPORT_VERSION,
        'datasink_name': datasink_name,
        'generated_at': now,
        'disk_summary': disk_summary,
        'daily_summary': daily_summary,
    }

    save_report(report, report_file)

    # Log summary
    logger.info(f"Storage report saved to {report_file}")
    logger.info(f"  Datasink: {datasink_name}")
    logger.info(f"  Disk: {disk_metrics['total_bytes'] / (1024**3):.1f} GB total, "
                f"{disk_metrics['free_bytes'] / (1024**3):.1f} GB free "
                f"({disk_metrics['used_bytes'] / disk_metrics['total_bytes'] * 100:.1f}% used)")
    logger.info(f"  SentinelCam: {total_image_bytes / (1024**3):.2f} GB images, "
                f"{total_csv_bytes / (1024**2):.1f} MB CSV")
    if len(daily_summary) > 0:
        date_count = daily_summary['date'].nunique()
        logger.info(f"  Coverage: {date_count} dates in daily_summary")


def main():
    config_file = os.path.join(os.path.expanduser("~"), "storage_analysis.yaml")
    if not os.path.isfile(config_file):
        print(f"Configuration file not found: {config_file}", file=sys.stderr)
        sys.exit(1)

    cfg = readConfig(config_file)

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logger.info(f"Storage analysis starting for datasink '{cfg['datasink_name']}'")
    try:
        run_analysis(cfg)
    except Exception:
        logger.exception("Storage analysis failed")
        sys.exit(1)
    logger.info("Storage analysis complete")


if __name__ == "__main__":
    main()
