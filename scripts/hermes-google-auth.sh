#!/usr/bin/env bash
# Sign Hermes' google-digest sidecar in to Google (read-only Gmail + Calendar) and write the token straight
# into its SOPS Secret: the plaintext only ever lives in this process, never on disk or the Hermes volume.
#   scripts/hermes-google-auth.sh [client_secret.json]
# Without an argument it reuses the OAuth client already in the Secret. Commit and push the result;
# reloader restarts Hermes with the new token.
set -euo pipefail
repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
exec uv run --quiet --no-project --with google-auth-oauthlib python - "$repo" "${1:-}" <<'EOF'
import json
import subprocess
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

repo, client_file = sys.argv[1:3]
out = "kubernetes/apps/ai/hermes/app/google-secret.sops.yaml"
scopes = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/calendar.readonly"]

if client_file:
    client = json.load(open(client_file))
else:
    old = json.loads(subprocess.run(
        ["sops", "--decrypt", "--extract", '["stringData"]["token.json"]', out],
        cwd=repo, check=True, capture_output=True, text=True).stdout)
    client = {"installed": {k: old[k] for k in ("client_id", "client_secret", "token_uri")} | {
        "auth_uri": "https://accounts.google.com/o/oauth2/auth"}}

# prompt=consent makes Google return a refresh token even when this client was approved before.
creds = InstalledAppFlow.from_client_config(client, scopes).run_local_server(port=0, prompt="consent")
# The sidecar mints its own access tokens from the refresh token, so the short-lived one is left out.
secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "hermes-google", "namespace": "ai"},
          "stringData": {"token.json": creds.to_json(strip=["token", "expiry"])}}
enc = subprocess.run(
    ["sops", "--encrypt", "--filename-override", out, "/dev/stdin"],
    cwd=repo, input=json.dumps(secret), check=True, capture_output=True, text=True).stdout
open(f"{repo}/{out}", "w").write(enc)
print(f"wrote {out}: commit and push it")
EOF
