#!/bin/bash
set -euo pipefail

cd /var/www/html

if ! php occ status >/dev/null 2>&1; then
    echo "WARNING: Nextcloud not ready, skipping cron job" >&2
    exit 0
fi

# App background jobs (memories, recognize, ...) all run inside cron.php
# Preview generation (Image.php) exhausts the image's 512M PHP limit nightly; the container allows 4Gi.
php -d memory_limit=1536M -f cron.php || echo "ERROR: cron.php failed" >&2

# Daily block. Runs take ~9min under concurrencyPolicy Forbid, so a fixed :00
# slot gets skipped when the previous run overruns; the stamp makes any run in
# the 02:xx UTC hour pick it up exactly once. If the whole hour was missed
# (rollout, NFS mount hang), the first run after it catches up.
# 10# forces decimal: bash reads "08"/"09" as invalid octal.
HOUR=$((10#$(date -u +%H)))
STAMP="/var/www/tmp/.daily-maintenance-$(date -u +%F)"
if [ "$HOUR" -ge 2 ] && [ ! -e "$STAMP" ]; then
    rm -f /var/www/tmp/.daily-maintenance-*
    touch "$STAMP"
    for c in "app:update --all" files:cleanup "files:scan --all"; do
        # shellcheck disable=SC2086  # word splitting on purpose: "cmd --flag" -> argv
        php occ $c || echo "WARNING: $c failed" >&2
    done
fi
