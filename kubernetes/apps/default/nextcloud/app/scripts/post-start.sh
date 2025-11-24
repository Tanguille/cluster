#!/bin/bash
set -euo pipefail

echo "Installing system dependencies..."
apt-get update && apt-get install -y --no-install-recommends \
  libimage-exiftool-perl \
  ffmpeg \
  imagemagick \
  libmagickcore-6.q16-6-extra \
  libmagickwand-6.q16-6 \
  nodejs \
  npm \
  || echo "WARNING: Failed to install some dependencies"

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

# Configure ffmpeg if available
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  FFMPEG_PATH=$(which ffmpeg)
  FFPROBE_PATH=$(which ffprobe)
  echo "ffmpeg found at: $FFMPEG_PATH"
  echo "ffprobe found at: $FFPROBE_PATH"

  # Configure ffmpeg path for Nextcloud previews
  su -s /bin/sh www-data -c "php occ config:system:set preview_ffmpeg_path --value=\"/usr/bin/ffmpeg\"" || true

  # Configure Memories-specific ffmpeg and ffprobe paths
  su -s /bin/sh www-data -c "php occ config:app:set memories vod.ffmpeg --value=\"/usr/bin/ffmpeg\"" || true
  su -s /bin/sh www-data -c "php occ config:app:set memories vod.ffprobe --value=\"/usr/bin/ffprobe\"" || true

  echo "ffmpeg paths configured for Nextcloud and Memories"

  # Run Memories video setup to finalize configuration
  su -s /bin/sh www-data -c "php occ memories:video-setup" || true
else
  echo "WARNING: ffmpeg or ffprobe not found in PATH"
fi

# Configure ImageMagick for Memories if available
if command -v convert >/dev/null 2>&1; then
  echo "ImageMagick found, configuring for Memories..."
  # Set ImageMagick path for Nextcloud
  su -s /bin/sh www-data -c "php occ config:system:set preview_imagick_path --value=\"/usr/bin/convert\"" || true
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

echo "Memories configuration completed"
