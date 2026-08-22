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
control="$(docker run --rm --entrypoint cat "$image" /usr/share/postgresql/18/extension/vector.control)"
actual="$(grep '^default_version' <<<"$control" | sed -E "s/.*= *'([^']+)'.*/\1/")"

echo "pinned image: ${image}"
echo "bundled vector version: ${actual}"

status=0
while IFS= read -r pin; do
  if [[ "$pin" != "$actual" ]]; then
    echo "::error file=${DATABASES_FILE}::vector pin '${pin}' does not match ${actual} bundled in ${image}"
    status=1
  fi
done < <(yq '.spec.inputs[].extensions[]? | select(.name == "vector") | .version' "$DATABASES_FILE")

exit "$status"
