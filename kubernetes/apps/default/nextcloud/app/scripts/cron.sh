#!/bin/bash
set -u

# Change to Nextcloud directory (required for occ commands)
cd /var/www/html || { echo "ERROR: Cannot cd to /var/www/html" >&2; exit 1; }

# Helper function to run occ commands with error handling
run_occ() {
  local cmd="$1"
  local warning_msg="${2:-${cmd} failed}"
  php occ "$cmd" || echo "WARNING: $warning_msg" >&2
}

# Helper function to check if an app is installed
app_installed() {
  php occ app:list | grep -q "$1"
}

# Helper function to run occ command if app is installed
run_if_app_installed() {
  local app="$1"
  local cmd="$2"
  local msg="${3:-Running $app operation...}"
  if app_installed "$app"; then
    echo "$msg"
    run_occ "$cmd"
  fi
}

echo "Starting Nextcloud cron job at $(date)"

if ! run_occ "status" >/dev/null 2>&1; then
  echo "WARNING: Nextcloud not ready, skipping cron job" >&2
  exit 0
fi

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
DAY_OF_WEEK=$(date +%u)

# Run database maintenance and cleanup every hour (at minute 0)
if [ "$MINUTE" = "00" ]; then
  echo "Running hourly maintenance operations..."
  run_occ "db:add-missing-indices"
  run_occ "db:add-missing-primary-keys"
  run_occ "db:add-missing-columns"
  run_occ "files:cleanup"
fi

# Run very expensive operations less frequently (once per day at 2 AM UTC)
# files:scan --all is VERY expensive and can take a long time
# Adjust the hour if needed (0-23) - note this is UTC time
if [ "$MINUTE" = "00" ] && [ "$HOUR" = "02" ]; then
  echo "Running daily expensive maintenance operations..."
  run_occ "maintenance:repair --include-expensive"
  run_occ "files:scan --all"
  run_if_app_installed "memories" "memories:index" "Running Memories indexing..."
fi

# Run Memories places setup weekly (Sunday at 3 AM UTC)
if [ "$MINUTE" = "00" ] && [ "$HOUR" = "03" ] && [ "$DAY_OF_WEEK" = "7" ]; then
  run_if_app_installed "memories" "memories:places-setup" "Running Memories places setup..."
fi

# Run Recognize face clustering weekly (Sunday at 4 AM UTC)
# This is expensive and should run after Memories places setup
if [ "$MINUTE" = "00" ] && [ "$HOUR" = "04" ] && [ "$DAY_OF_WEEK" = "7" ]; then
  run_if_app_installed "recognize" "recognize:cluster-faces" "Running Recognize face clustering..."
fi

# Run Recognize background job (every 5 minutes to process queued files)
# This processes face recognition, object detection, landmark recognition, and audio tagging queues
# The app has internal locking to prevent concurrent execution
run_if_app_installed "recognize" "recognize:recrawl" "Running Recognize background job..."

# Run Face Recognition background job (every 15 minutes as recommended by maintainer)
# Default order: clustering first (Step 5), then new face detection (Steps 6-8)
# New photos are analyzed in one run and grouped in the next run
# Manual sorting/naming is preserved - the app won't overwrite manually configured faces
# The job will stop after 15 minutes (timeout) and continue in the next run
# This distributes the load and prevents the job from running indefinitely
if [ "$((MINUTE % 15))" = "0" ]; then
  if app_installed "facerecognition"; then
    echo "Running Face Recognition background job (will stop after 15 minutes)..."
    # The app has internal locking (LockTask) to prevent concurrent execution
    # If a previous job is still running, this will fail gracefully due to the lock
    run_occ "face:background_job" "face:background_job failed (may be locked by another instance)"
  fi
fi

# Run Face Recognition album sync (every hour at minute 15)
# This syncs photo albums in the Photos app with recognized faces
# Albums are editable in Photos app, but changes are reverted on next sync
if [ "$MINUTE" = "15" ]; then
  run_if_app_installed "facerecognition" "face:sync-albums" "Running Face Recognition album sync..."
fi

echo "Nextcloud cron job completed at $(date)"
