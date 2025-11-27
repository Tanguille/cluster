#!/bin/bash
set -uo pipefail

# Log all output to a file for debugging (create log dir if needed)
LOG_FILE="/var/log/post-start.log"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1

# Helper function to run occ commands as www-data
run_occ() {
  su -s /bin/sh www-data -c "php occ $*" || true
}

# Helper function to set config values
set_config() {
  local type="$1"
  shift
  run_occ "config:$type:set $*"
}

# Helper function to configure tool path if available
configure_tool_path() {
  local tool="$1"
  local config_key="$2"
  local config_type="${3:-system}"
  if command -v "$tool" >/dev/null 2>&1; then
    local tool_path
    tool_path=$(command -v "$tool")
    echo "$tool found at: $tool_path"
    set_config "$config_type" "$config_key" --value="$tool_path"
    return 0
  else
    echo "WARNING: $tool not found in PATH"
    return 1
  fi
}

echo "=== Post-start script started at $(date) ==="
echo "Installing system dependencies..."

export DEBIAN_FRONTEND=noninteractive

# Try to install packages, but don't fail the entire script if some are missing
if ! apt-get update; then
  echo "ERROR: apt-get update failed" >&2
  exit 1
fi

# Install packages one by one to see which ones fail
for pkg in libimage-exiftool-perl ffmpeg imagemagick libmagickcore-7.q16-10 libmagickwand-7.q16-10 nodejs npm; do
  echo "Installing $pkg..."
  if apt-get install -y --no-install-recommends "$pkg" 2>&1; then
    echo "Successfully installed $pkg"
  else
    echo "WARNING: Failed to install $pkg (package may not exist or have a different name)" >&2
  fi
done

echo "Waiting for Nextcloud to be ready..."
# Wait for Nextcloud to be fully initialized (max 60 seconds)
for i in {1..30}; do
  if run_occ "status" >/dev/null 2>&1; then
    echo "Nextcloud is ready"
    break
  fi
  echo "Waiting for Nextcloud... ($i/30)"
  sleep 2
done

# Fix appdata directory structure for JSCombiner
echo "Creating appdata directories..."
DATA_DIR="/var/www/data"
if [ -d "$DATA_DIR" ]; then
  # Find all appdata directories and ensure js/core subdirectories exist
  while IFS= read -r appdata_dir; do
    [ -z "$appdata_dir" ] && continue
    core_dir="${appdata_dir}/js/core"
    if [ ! -d "$core_dir" ]; then
      echo "Creating directory: $core_dir"
      mkdir -p "$core_dir" || { echo "ERROR: Failed to create $core_dir"; continue; }
      chown www-data:www-data "$core_dir" || echo "WARNING: Failed to chown $core_dir"
      chmod 755 "$core_dir" || echo "WARNING: Failed to chmod $core_dir"
    fi
  done < <(find "$DATA_DIR" -type d -name "appdata_*" 2>/dev/null) || true

  # Also ensure the main appdata structure has correct permissions
  chown -R www-data:www-data "$DATA_DIR" || echo "WARNING: Failed to chown $DATA_DIR"
  find "$DATA_DIR" -type d -exec chmod 755 {} + 2>/dev/null || echo "WARNING: Failed to chmod directories in $DATA_DIR"
  find "$DATA_DIR" -type f -exec chmod 644 {} + 2>/dev/null || echo "WARNING: Failed to chmod files in $DATA_DIR"
fi

# Configure ImageMagick for Memories if available
if configure_tool_path "convert" "preview_imagick_path" "system"; then
  echo "ImageMagick configured for Memories"
fi

# Configure Memories-specific settings
echo "Configuring Memories app settings..."
set_config "app" "memories enable_transitions" --value="yes"
set_config "app" "memories preview_max_x" --value="2048"
set_config "app" "memories preview_max_y" --value="2048"

# Configure exiftool path for Memories if available
configure_tool_path "exiftool" "memories exiftool" "app"

echo "Configuration completed"
