#!/bin/bash
# Clean up video exports older than 72 hours

EXPORT_DIR="/var/www/sentinelcam_exports"
RETENTION_DAYS=3

# Find and delete old files
find "$EXPORT_DIR" -type f -name "*.mp4" -mtime +$RETENTION_DAYS -delete

# Log cleanup
DELETED_COUNT=$(find "$EXPORT_DIR" -type f -name "*.mp4" -mtime +$RETENTION_DAYS | wc -l)
if [ $DELETED_COUNT -gt 0 ]; then
    logger "SentinelCam: Cleaned up $DELETED_COUNT expired video exports"
fi
