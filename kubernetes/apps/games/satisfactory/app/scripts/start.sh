#!/bin/bash
# FicsitRemoteMonitoring is a native plugin: when a game update breaks it, it segfaults the whole
# server at boot (porisius/FicsitRemoteMonitoring#330). After 3 starts of the same pod the mod is
# removed so the server comes up without it. mods.sh reinstalls it and resets the count on every new
# pod (the daily restart.yaml rollout), so a fixed release returns without a change here.
# ponytail: counts every start, not only FRM crashes. Parse the crash log if that ever bites.
set -euo pipefail

STARTS=/config/.frm-starts
echo >>"$STARTS"
if (($(wc -l <"$STARTS") > 3)); then
    echo "WARNING: more than 3 starts in this pod, starting without FicsitRemoteMonitoring" >&2
    rm -rf /config/gamefiles/FactoryGame/Mods/FicsitRemoteMonitoring \
        /config/gamefiles/FactoryGame/Mods/GameFeatures/FicsitRemoteMonitoring
fi
exec /init.sh
