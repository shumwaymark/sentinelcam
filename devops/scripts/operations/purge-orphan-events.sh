#!/bin/bash
# purge-orphan-events.sh
#
# Find event UUIDs present in detail CSV files or image JPG files but absent
# from the camwatcher.csv index for a given date, then delete those files.
#
# Both populations (CSV dir and image dir) are checked independently against
# the index, so events that exist only as images (no CSV) or only as CSVs
# (no images) are still detected and removed.
#
# This is for the scenario where camwatcher was restarted with a backlog of
# DelEvt operations pending in the CSVindex queue: the index entries are already
# gone, but the per-event CSV and image files on disk were never cleaned up.
#
# Usage:
#   purge-orphan-events.sh YYYY-MM-DD [--dry-run]
#
# --dry-run  List what would be deleted without removing anything.

set -euo pipefail

DATE="${1:-}"
DRY_RUN=false
if [[ "${2:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

if [[ -z "$DATE" ]]; then
    echo "Usage: $0 YYYY-MM-DD [--dry-run]"
    exit 1
fi

# Paths per sentinelcam_standards.yaml conventions
BASE_DIR="${HOME}/sentinelcam"
CSV_DIR="${BASE_DIR}/camwatcher/${DATE}"
IMG_DIR="${BASE_DIR}/images/${DATE}"
INDEX_FILE="${CSV_DIR}/camwatcher.csv"

if [[ ! -d "$CSV_DIR" ]]; then
    echo "ERROR: No camwatcher data directory for ${DATE}: ${CSV_DIR}"
    exit 1
fi

if [[ ! -f "$INDEX_FILE" ]]; then
    echo "ERROR: No index file found: ${INDEX_FILE}"
    exit 1
fi

# Extract indexed event UUIDs — event is column 4, no header row
INDEXED=$(awk -F',' '{print $4}' "$INDEX_FILE" | sort -u)

# Events found in detail CSV filenames: {EventID}_{type}.csv  (exclude index itself)
CSV_EVENTS=$(find "$CSV_DIR" -maxdepth 1 -name "*.csv" ! -name "camwatcher.csv" \
    | sed 's|.*/\([^_]*\)_[^/]*\.csv|\1|' \
    | sort -u)

# Events found in image filenames: {EventID}_{Timestamp}.jpg
IMG_EVENTS=""
if [[ -d "$IMG_DIR" ]]; then
    IMG_EVENTS=$(find "$IMG_DIR" -maxdepth 1 -name "*.jpg" \
        | sed 's|.*/\([^_]*\)_[^/]*\.jpg|\1|' \
        | sort -u)
fi

# Union of both populations
ALL_EVENTS=$(printf '%s\n%s\n' "$CSV_EVENTS" "$IMG_EVENTS" | sort -u | grep -v '^$' || true)

if [[ -z "$ALL_EVENTS" ]]; then
    echo "No detail CSV or image files found for ${DATE}."
    exit 0
fi

echo "On-disk summary for ${DATE}:"
echo "  CSV detail files : $(echo "$CSV_EVENTS" | grep -c . || echo 0) unique event(s)"
echo "  Image files      : $(echo "$IMG_EVENTS" | grep -c . || echo 0) unique event(s)"
echo "  Index entries    : $(echo "$INDEXED"    | grep -c . || echo 0) unique event(s)"
echo ""

# CSV orphans: in CSV dir but not in index
CSV_ORPHANS=$(comm -23 <(echo "$CSV_EVENTS") <(echo "$INDEXED") | grep -v '^$' || true)
# Image orphans: in image dir but not in index
IMG_ORPHANS=""
if [[ -n "$IMG_EVENTS" ]]; then
    IMG_ORPHANS=$(comm -23 <(echo "$IMG_EVENTS") <(echo "$INDEXED") | grep -v '^$' || true)
fi

# Full union of orphans across both populations
ORPHANS=$(printf '%s\n%s\n' "$CSV_ORPHANS" "$IMG_ORPHANS" | sort -u | grep -v '^$' || true)

if [[ -z "$ORPHANS" ]]; then
    echo "No orphaned events found for ${DATE}."
    exit 0
fi

ORPHAN_COUNT=$(echo "$ORPHANS" | grep -c . || echo 0)
echo "Found ${ORPHAN_COUNT} orphaned event(s) for ${DATE}:"
echo "$ORPHANS"
echo ""

DELETED_CSV=0
DELETED_JPG=0

for EVENT in $ORPHANS; do
    # Per-event detail CSV files
    mapfile -t CSV_FILES < <(find "$CSV_DIR" -maxdepth 1 -name "${EVENT}_*.csv" 2>/dev/null || true)
    # Per-event image files
    mapfile -t IMG_FILES < <([[ -d "$IMG_DIR" ]] && find "$IMG_DIR" -maxdepth 1 -name "${EVENT}_*.jpg" 2>/dev/null || true)

    if $DRY_RUN; then
        echo "[dry-run] ${EVENT}:"
        for f in "${CSV_FILES[@]+"${CSV_FILES[@]}"}"; do echo "  CSV  $f"; done
        for f in "${IMG_FILES[@]+"${IMG_FILES[@]}"}"; do echo "  JPG  $f"; done
    else
        echo "Removing ${EVENT}:"
        for f in "${CSV_FILES[@]+"${CSV_FILES[@]}"}"; do
            rm -f "$f"
            echo "  CSV  $f"
            DELETED_CSV=$((DELETED_CSV + 1))
        done
        for f in "${IMG_FILES[@]+"${IMG_FILES[@]}"}"; do
            rm -f "$f"
            echo "  JPG  $f"
            DELETED_JPG=$((DELETED_JPG + 1))
        done
    fi
done

echo ""
if $DRY_RUN; then
    echo "[dry-run] ${ORPHAN_COUNT} orphaned event(s) identified. No files removed."
else
    echo "Done. ${ORPHAN_COUNT} event(s) purged — ${DELETED_CSV} CSV file(s), ${DELETED_JPG} JPG file(s) deleted."
fi
