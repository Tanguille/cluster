#!/bin/bash
set -euo pipefail

echo "Installing system dependencies..."
apt-get update && apt-get install -y --no-install-recommends \
  libimage-exiftool-perl \
  ffmpeg \
  imagemagick \
  libmagickcore-7.q16-10-extra \
  libmagickwand-7.q16-10 \
  nodejs \
  npm

echo "Waiting for Nextcloud to be ready..."
# Wait for Nextcloud to be fully initialized (max 60 seconds)
for i in {1..30}; do
  if su -s /bin/sh www-data -c "php occ status" >/dev/null 2>&1; then
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
    js_dir="${appdata_dir}/js"
    core_dir="${js_dir}/core"
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

# Configure ffmpeg if available
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  FFMPEG_PATH=$(command -v ffmpeg)
  FFPROBE_PATH=$(command -v ffprobe)
  echo "ffmpeg found at: $FFMPEG_PATH"
  echo "ffprobe found at: $FFPROBE_PATH"

  # Configure ffmpeg path for Nextcloud previews
  su -s /bin/sh www-data -c "php occ config:system:set preview_ffmpeg_path --value=\"$FFMPEG_PATH\"" || true

  # Configure Memories-specific ffmpeg and ffprobe paths
  su -s /bin/sh www-data -c "php occ config:app:set memories vod.ffmpeg --value=\"$FFMPEG_PATH\"" || true
  su -s /bin/sh www-data -c "php occ config:app:set memories vod.ffprobe --value=\"$FFPROBE_PATH\"" || true

  echo "ffmpeg paths configured for Nextcloud and Memories"

  # Run Memories video setup to finalize configuration
  su -s /bin/sh www-data -c "php occ memories:video-setup" || true
else
  echo "WARNING: ffmpeg or ffprobe not found in PATH"
fi

# Configure ImageMagick for Memories if available
if command -v convert >/dev/null 2>&1; then
  CONVERT_PATH=$(command -v convert)
  echo "ImageMagick found at: $CONVERT_PATH, configuring for Memories..."
  # Set ImageMagick path for Nextcloud
  su -s /bin/sh www-data -c "php occ config:system:set preview_imagick_path --value=\"$CONVERT_PATH\"" || true
else
  echo "WARNING: ImageMagick (convert) not found in PATH"
fi

# Configure Memories-specific settings
echo "Configuring Memories app settings..."
# Enable Memories indexing
su -s /bin/sh www-data -c "php occ config:app:set memories enable_transitions --value=\"yes\"" || true
# Set preview quality for Memories
su -s /bin/sh www-data -c "php occ config:app:set memories preview_max_x --value=\"2048\"" || true
su -s /bin/sh www-data -c "php occ config:app:set memories preview_max_y --value=\"2048\"" || true
# Enable video transcoding if ffmpeg is available
if command -v ffmpeg >/dev/null 2>&1; then
  su -s /bin/sh www-data -c "php occ config:app:set memories enable_video_transcoding --value=\"yes\"" || true
fi

# Configure exiftool path for Memories if available
if command -v exiftool >/dev/null 2>&1; then
  EXIFTOOL_PATH=$(command -v exiftool)
  echo "exiftool found at: $EXIFTOOL_PATH"
  # Set exiftool path for Memories app
  su -s /bin/sh www-data -c "php occ config:app:set memories exiftool --value=\"$EXIFTOOL_PATH\"" || true
else
  echo "WARNING: exiftool not found in PATH"
fi
echo "Configuration completed"
