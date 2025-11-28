#!/bin/bash
set -u

LOG_FILE="/var/log/post-start.log"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

log() {
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$*"
  echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

run_occ() {
  local cmd="cd /var/www/html && php occ"
  for arg in "$@"; do
    cmd="$cmd $(printf '%q' "$arg")"
  done
  su -s /bin/sh www-data -c "$cmd" 2>/dev/null || true
}

set_config() {
  local type="$1"
  shift
  run_occ "config:$type:set" "$@"
}

find_tool() {
  local tool="$1"
  local path
  path=$(command -v "$tool" 2>/dev/null)
  [ -n "$path" ] && echo "$path" && return
  find /usr/bin /usr/local/bin -maxdepth 2 -name "$tool" -type f -executable 2>/dev/null | head -1
}

log "=== Post-start script started ==="

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || log "WARNING: apt-get update failed"

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
  [ "$i" -lt 30 ] && sleep 2
done
[ "$i" -eq 30 ] && log "WARNING: Nextcloud not ready after 60 seconds"



log "Configuring tools..."
tool_path=$(find_tool "convert")
[ -n "$tool_path" ] && set_config "system" "preview_imagick_path" --value="$tool_path"

tool_path=$(find_tool "exiftool")
if [ -n "$tool_path" ]; then
  log "Found exiftool at $tool_path"
  set_config "app" "memories exiftool" --value="$tool_path"
else
  log "WARNING: exiftool not found"
fi

if run_occ "status" >/dev/null 2>&1; then
  set_config "app" "memories enable_transitions" --value="yes"
  set_config "app" "memories preview_max_x" --value="2048"
  set_config "app" "memories preview_max_y" --value="2048"
fi

log "=== Post-start script completed ==="
