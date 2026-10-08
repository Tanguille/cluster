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
  selected_profile: "server", version: 0}' > "$HOME/.local/share/ficsit/profiles.json"
"$HOME/ficsit" installation add /config/gamefiles server
"$HOME/ficsit" apply
