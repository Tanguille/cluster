#!/usr/bin/env bash
# Fails if any `vector` extension pin in resourceset.yaml doesn't match the pgvector version
# actually bundled in the pinned CNPG postgresql image. Renovate can't catch this itself: the
# base image's own tag/digest carries no signal about which pgvector version it ships, so a
# bump can drift the pin silently (see kubernetes/apps/database/cloudnative-pg/databases/
# resourceset.yaml's vector comment for how this is normally checked by hand).
set -euo pipefail

CLUSTER_FILE="kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml"
DATABASES_FILE="kubernetes/apps/database/cloudnative-pg/databases/resourceset.yaml"

image="$(yq '.spec.imageName' "$CLUSTER_FILE")"
# Tag looks like "18.6-standard-trixie" — the extension path is keyed by PG major, not the
# full version, so a PG19 image must not silently check PG18's (nonexistent) path.
pg_major="$(sed -E 's/.*:([0-9]+)\..*/\1/' <<<"$image")"
control="$(docker run --rm --entrypoint cat "$image" "/usr/share/postgresql/${pg_major}/extension/vector.control")"
actual="$(grep '^default_version' <<<"$control" | sed -E "s/.*= *'([^']+)'.*/\1/")"

echo "pinned image: ${image}"
echo "bundled vector version: ${actual}"

# Capture to a variable first: a process substitution's exit status never reaches `set -e`
# (see scripts/talos-kernel-build.sh for the same convention).
pins="$(yq '.spec.inputs[].extensions[]? | select(.name == "vector") | .version' "$DATABASES_FILE")"

status=0
while IFS= read -r pin; do
  if [[ "$pin" != "$actual" ]]; then
    echo "::error file=${DATABASES_FILE}::vector pin '${pin}' does not match ${actual} bundled in ${image}"
    status=1
  fi
done <<<"$pins"

exit "$status"
