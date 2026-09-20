#!/bin/bash
set -euo pipefail

cd /var/www/html

# Plain call, not a helper that swallows the exit code: the guard must be able to fire
if ! php occ status >/dev/null 2>&1; then
    echo "WARNING: Nextcloud not ready, skipping cron job" >&2
    exit 0
fi

# App background jobs (memories, recognize, ...) all run inside cron.php
php -f cron.php || echo "ERROR: cron.php failed" >&2

# Daily block. Runs take ~9min under concurrencyPolicy Forbid, so a fixed :00
# slot gets skipped when the previous run overruns; the stamp makes any run in
# the 02:xx UTC hour pick it up exactly once.
STAMP="/var/www/tmp/.daily-maintenance-$(date -u +%F)"
if [ "$(date -u +%H)" = "02" ] && [ ! -e "$STAMP" ]; then
    rm -f /var/www/tmp/.daily-maintenance-*
    touch "$STAMP"
    for c in "app:update --all" files:cleanup "files:scan --all"; do
        # shellcheck disable=SC2086  # word splitting on purpose: "cmd --flag" -> argv
        php occ $c || echo "WARNING: $c failed" >&2
    done
fi
