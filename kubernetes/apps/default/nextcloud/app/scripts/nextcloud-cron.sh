#!/bin/sh
set -u  # Fail on undefined variables

# Change to Nextcloud directory (required for occ commands)
cd /var/www/html || { echo "ERROR: Cannot cd to /var/www/html" >&2; exit 1; }

echo "Starting Nextcloud cron job at $(date)"

# Run standard Nextcloud cron (every 5 minutes)
# This is the main cron job that Nextcloud requires
if ! php -f /var/www/html/cron.php; then
  echo "ERROR: Nextcloud cron.php failed" >&2
  # Don't exit - allow maintenance operations to run even if cron.php fails
fi

# Run maintenance operations based on time
# Note: Container timezone may be UTC, adjust hour accordingly
MINUTE=$(date +%M)
HOUR=$(date +%H)

# Run database maintenance and cleanup every hour (at minute 0)
if [ "$MINUTE" = "00" ]; then
  echo "Running hourly maintenance operations..."

  # Database maintenance (safe to run, adds missing indices/keys/columns)
  php occ db:add-missing-indices || echo "WARNING: db:add-missing-indices failed" >&2
  php occ db:add-missing-primary-keys || echo "WARNING: db:add-missing-primary-keys failed" >&2
  php occ db:add-missing-columns || echo "WARNING: db:add-missing-columns failed" >&2

  # File cleanup (removes orphaned file entries)
  php occ files:cleanup || echo "WARNING: files:cleanup failed" >&2

fi

# Run very expensive operations less frequently (once per day at 2 AM UTC)
# files:scan --all is VERY expensive and can take a long time
# Adjust the hour if needed (0-23) - note this is UTC time
if [ "$MINUTE" = "00" ] && [ "$HOUR" = "02" ]; then
  echo "Running daily expensive maintenance operations..."

  php occ maintenance:repair --include-expensive || echo "WARNING: maintenance:repair failed" >&2

  php occ files:scan --all || echo "WARNING: files:scan --all failed" >&2

  # Run Memories indexing (if Memories app is installed)
  if php occ app:list | grep -q "memories"; then
    echo "Running Memories indexing..."
    php occ memories:index || echo "WARNING: memories:index failed" >&2
  fi
fi

# Run Memories places setup weekly (Sunday at 3 AM UTC)
if [ "$MINUTE" = "00" ] && [ "$HOUR" = "03" ] && [ "$(date +%u)" = "7" ]; then
  if php occ app:list | grep -q "memories"; then
    echo "Running Memories places setup..."
    php occ memories:places-setup || echo "WARNING: memories:places-setup failed" >&2
  fi
fi

# Run Recognize face clustering weekly (Sunday at 4 AM UTC)
# This is expensive and should run after Memories places setup
if [ "$MINUTE" = "00" ] && [ "$HOUR" = "04" ] && [ "$(date +%u)" = "7" ]; then
  if php occ app:list | grep -q "recognize"; then
    echo "Running Recognize face clustering..."
    php occ recognize:cluster-faces || echo "WARNING: recognize:cluster-faces failed" >&2
  fi
fi

# Run Recognize classification regularly to queue background jobs
# This processes new files for face recognition, object detection, audio, and video tagging
# Using --retry flag to only process untagged files (more efficient)
# Run every 5 minutes (when cron runs) to keep the queue populated
if php occ app:list | grep -q "recognize"; then
  echo "Running Recognize classification to queue background jobs..."
  php occ recognize:classify --retry || echo "WARNING: recognize:classify failed" >&2
fi

# Run Face Recognition background job (every 15 minutes as recommended by maintainer)
# Default order: clustering first (Step 5), then new face detection (Steps 6-8)
# New photos are analyzed in one run and grouped in the next run
# Manual sorting/naming is preserved - the app won't overwrite manually configured faces
# The job will stop after 15 minutes (timeout) and continue in the next run
# This distributes the load and prevents the job from running indefinitely
if [ "$((MINUTE % 15))" = "0" ]; then
  if php occ app:list | grep -q "facerecognition"; then
    echo "Running Face Recognition background job (will stop after 15 minutes)..."
    # The app has internal locking (LockTask) to prevent concurrent execution
    # If a previous job is still running, this will fail gracefully due to the lock
    php occ face:background_job --timeout 900 || echo "WARNING: face:background_job failed (may be locked by another instance)" >&2
  fi
fi

# Run Face Recognition album sync (every hour at minute 15)
# This syncs photo albums in the Photos app with recognized faces
# Albums are editable in Photos app, but changes are reverted on next sync
if [ "$MINUTE" = "15" ]; then
  if php occ app:list | grep -q "facerecognition"; then
    echo "Running Face Recognition album sync..."
    php occ face:sync-albums || echo "WARNING: face:sync-albums failed" >&2
  fi
fi

echo "Nextcloud cron job completed at $(date)"
