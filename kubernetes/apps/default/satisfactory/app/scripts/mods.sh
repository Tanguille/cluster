#!/bin/bash
set -euo pipefail

# First start: steamcmd hasn't installed the game yet, so mods land on the next restart.
if [[ ! -f /config/gamefiles/FactoryServer.sh ]]; then
    echo "game not installed yet, skipping mods"
    exit 0
fi

# Fresh state each run: `installation add` fails if a retry finds the previous one.
rm -rf "$HOME"
mkdir -p "$HOME/.local/share/ficsit"
curl -fsSL -o "$HOME/ficsit" "https://github.com/satisfactorymodding/ficsit-cli/releases/download/$FICSIT_VERSION/ficsit_linux_amd64"
echo "$FICSIT_SHA256  $HOME/ficsit" | sha256sum -c -
chmod +x "$HOME/ficsit"

# ficsit-cli has no non-interactive "profile mod add", so write the profile directly.
jq -n --arg mods "$MODS" '{profiles: {server: {name: "server", required_targets: null,
  mods: ($mods | split(" ") | map({(.): {version: ">=0.0.0", enabled: true}}) | add)}},
  selected_profile: "server", version: 0}' >"$HOME/.local/share/ficsit/profiles.json"
"$HOME/ficsit" installation add /config/gamefiles server
"$HOME/ficsit" apply

# FRM's web server (port 8080) is off by default and headless servers have no
# console to run `/frm http start`, so seed the autostart setting into the
# FGUserSettings store the mod reads since Satisfactory 1.2. Idempotent: the
# game rewrites this file, and the grep guard keeps repeat starts a no-op.
if [[ " $MODS " == *" FicsitRemoteMonitoring "* ]]; then
    FRM_INI=/config/gamefiles/FactoryGame/Saved/Config/LinuxServer/GameUserSettings.ini
    if [[ -f $FRM_INI ]] && ! grep -q "FicsitRemoteMonitoring.Server.uWS.Autostart" "$FRM_INI"; then
        if grep -q '^mIntValues=(' "$FRM_INI"; then
            sed -i 's/^mIntValues=(/mIntValues=(("FicsitRemoteMonitoring.Server.uWS.Autostart", 1),(/' "$FRM_INI"
        else
            sed -i '/^\[\/Script\/FactoryGame\.FGGameUserSettings\]$/a mIntValues=(("FicsitRemoteMonitoring.Server.uWS.Autostart", 1))' "$FRM_INI"
        fi
        echo "Seeded FRM web server autostart into GameUserSettings.ini"
    fi
fi
