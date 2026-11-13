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

# FRM's web server is off by default and headless servers have no console for `/frm http start`,
# so force its autostart on. The game rewrites this file, so every start re-asserts it.
FRM_INI=/config/gamefiles/FactoryGame/Saved/Config/LinuxServer/GameUserSettings.ini
FRM_KEY='"FicsitRemoteMonitoring.Server.uWS.Autostart"'
if [[ -f $FRM_INI ]]; then
    # Flip an existing 0 to 1, else prepend to the tuple list (or fill the empty "()"). "b"/"t" stop after the first hit.
    sed -i -e "s/($FRM_KEY, *0)/($FRM_KEY, 1)/" -e "/$FRM_KEY/b" \
        -e "s/^mIntValues=()/mIntValues=(($FRM_KEY, 1))/;t" \
        -e "s/^mIntValues=(/mIntValues=(($FRM_KEY, 1),/;t" "$FRM_INI"
    grep -q '^mIntValues=' "$FRM_INI" ||
        sed -i "/^\[\/Script\/FactoryGame\.FGGameUserSettings\]$/a mIntValues=(($FRM_KEY, 1))" "$FRM_INI"
fi
