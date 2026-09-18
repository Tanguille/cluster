#!/bin/bash
set -euo pipefail

# ffmpeg is the only binary missing from the image that has a consumer
# (Movie/MP4 previews, memories.vod.ffmpeg). Memories and recognize ship their own
# exiftool/node; imagick is a compiled-in PHP extension.
# ponytail: apt on every start (~50s); bake into a custom image if restarts get frequent.
if ! dpkg-query -W ffmpeg >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends ffmpeg >/dev/null
fi
