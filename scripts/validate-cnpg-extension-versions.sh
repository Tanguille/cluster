#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=${REPO_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}
YQ_BIN=${YQ_BIN:-yq}
CRANE_BIN=${CRANE_BIN:-crane}
TAR_BIN=${TAR_BIN:-tar}

error() {
  printf 'error: %s\n' "$*" >&2
}

require_command() {
  local name=$1
  if ! command -v "$name" >/dev/null 2>&1; then
    error "required command not found: $name"
    exit 1
  fi
}

require_command "$YQ_BIN"
require_command "$CRANE_BIN"
require_command "$TAR_BIN"

cluster_file=$REPO_ROOT/kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml
database_dir=$REPO_ROOT/kubernetes/apps/database/cloudnative-pg/databases

if [[ ! -f $cluster_file ]]; then
  error "cluster manifest not found: $cluster_file"
  exit 1
fi
if [[ ! -d $database_dir ]]; then
  error "database manifest directory not found: $database_dir"
  exit 1
fi

image_name=$("$YQ_BIN" -r '.spec.imageName // ""' "$cluster_file")
if [[ -z $image_name ]]; then
  error "cluster manifest has no spec.imageName: $cluster_file"
  exit 1
fi

vchord_reference=$(
  "$YQ_BIN" -r '.spec.postgresql.extensions[] | select(.name == "vchord") | .image.reference // ""' "$cluster_file"
)
if [[ -z $vchord_reference ]]; then
  error "cluster manifest has no vchord image reference: $cluster_file"
  exit 1
fi
if [[ ! $vchord_reference =~ :pg18-v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  error "vchord image reference does not have a pg18-vX.Y.Z tag: $vchord_reference"
  exit 1
fi
expected_vchord_version=${BASH_REMATCH[1]}

vector_control=$(
  "$CRANE_BIN" export --platform linux/amd64 "$image_name" - |
    "$TAR_BIN" -xOf - usr/share/postgresql/18/extension/vector.control
)
expected_vector_version=''
while IFS= read -r control_line; do
  if [[ $control_line =~ ^[[:space:]]*default_version[[:space:]]*=[[:space:]]*[\'\"]?([^\'\"[:space:]]+)[\'\"]?[[:space:]]*$ ]]; then
    expected_vector_version=${BASH_REMATCH[1]}
    break
  fi
done <<< "$vector_control"
if [[ -z $expected_vector_version ]]; then
  error "could not parse default_version from vector.control"
  exit 1
fi

validated=0
shopt -s nullglob
database_files=("$database_dir"/*.yaml)
shopt -u nullglob
for database_file in "${database_files[@]}"; do
  entries=$("$YQ_BIN" -r '
    . as $document |
    ($document.metadata.name // "<unnamed>") as $database |
    (($document.spec.extensions // [])[] |
      select(.name == "vector" or .name == "vchord") |
      [$database, .name, (has("version") | tostring), (.version // "")] |
      @tsv)
  ' "$database_file")

  if [[ -z $entries ]]; then
    continue
  fi

  while IFS=$'\t' read -r database extension has_version version; do
    validated=$((validated + 1))
    if [[ $has_version != true || -z $version ]]; then
      error "$database $extension extension is missing version"
      exit 1
    fi

    expected_version=$expected_vector_version
    if [[ $extension == vchord ]]; then
      expected_version=$expected_vchord_version
    fi
    if [[ $version != "$expected_version" ]]; then
      error "$database $extension version $version does not match expected $expected_version"
      exit 1
    fi
    printf '%s/%s/%s validated\n' "$database" "$extension" "$version"
  done <<< "$entries"
done

if (( validated == 0 )); then
  error "no Database CR declares vector or vchord"
  exit 1
fi
