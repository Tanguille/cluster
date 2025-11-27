#!/bin/bash
set -u

# Setup dual logging: stdout/stderr for postStart hook + log file
LOG_FILE="/var/log/post-start.log"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

# Function to output to both stdout and log file
log() {
  echo "$*"
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE" 2>/dev/null || true
}

run_occ() {
  su -s /bin/sh www-data -c "cd /var/www/html && php occ $*" 2>/dev/null || true
}

set_config() {
  local type="$1"
  shift
  run_occ "config:$type:set $*"
}

configure_tool() {
  local tool="$1"
  local key="$2"
  local type="${3:-system}"
  command -v "$tool" >/dev/null 2>&1 && set_config "$type" "$key" --value="$(command -v "$tool")"
}

log "=== Post-start script started at $(date) ==="
log "Installing system dependencies..."

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

for pkg in libimage-exiftool-perl ffmpeg imagemagick libmagickcore-7.q16-10 libmagickwand-7.q16-10 nodejs npm; do
  if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
    log "Installing $pkg..."
    apt-get install -y --no-install-recommends "$pkg" >/dev/null 2>&1 || log "WARNING: Failed to install $pkg"
  fi
done

log "Waiting for Nextcloud to be ready..."
for i in {1..30}; do
  if run_occ "status" >/dev/null 2>&1; then
    log "Nextcloud is ready"
    break
  fi
  [ $i -eq 30 ] && log "WARNING: Nextcloud not ready after 60 seconds"
  sleep 2
done



# Configure tools and settings
configure_tool "convert" "preview_imagick_path" "system"
configure_tool "exiftool" "memories exiftool" "app"

if run_occ "status" >/dev/null 2>&1; then
  set_config "app" "memories enable_transitions" --value="yes"
  set_config "app" "memories preview_max_x" --value="2048"
  set_config "app" "memories preview_max_y" --value="2048"
fi

log "=== Post-start script completed at $(date) ==="
