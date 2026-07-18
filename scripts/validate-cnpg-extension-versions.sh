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
vector_reference=$(
  "$YQ_BIN" -r '.spec.postgresql.extensions[] | select(.name == "vector") | .image.reference // ""' "$cluster_file"
)
if [[ -z $vector_reference ]]; then
  error "cluster manifest has no vector image reference: $cluster_file"
  exit 1
fi
if [[ $vchord_reference != "$vector_reference" ]]; then
  error "vchord and vector image references differ: $vchord_reference vs $vector_reference"
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
vector_targets=0
vchord_targets=0
memini_vector_present=0
memini_vchord_present=0
shopt -s nullglob
database_files=("$database_dir"/*.yaml)
shopt -u nullglob
for database_file in "${database_files[@]}"; do
  # shellcheck disable=SC2016
  # This single-quoted string is a yq program; its $document/$database names are yq variables.
  entries=$("$YQ_BIN" -r '
    . as $document |
    ($document.metadata.name // "<unnamed>") as $database |
    (($document.spec.extensions // [])[] |
      select(.name == "vector" or .name == "vchord") |
      [$database, .name, (.ensure // "unset"), (has("version") | tostring), (.version // "")] |
      @tsv)
  ' "$database_file")

  if [[ -z $entries ]]; then
    continue
  fi

  while IFS=$'\t' read -r database extension ensure has_version version; do
    if [[ $database == memini && $extension == vector && $ensure == present ]]; then
      memini_vector_present=1
    fi
    if [[ $database == memini && $extension == vchord && $ensure == present ]]; then
      memini_vchord_present=1
    fi
    if [[ $ensure == absent ]]; then
      continue
    fi
    validated=$((validated + 1))
    if [[ $extension == vector ]]; then
      vector_targets=$((vector_targets + 1))
    else
      vchord_targets=$((vchord_targets + 1))
    fi
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

if (( memini_vector_present == 0 )); then
  error "memini vector extension must declare ensure: present"
  exit 1
fi
if (( memini_vchord_present == 0 )); then
  error "memini vchord extension must declare ensure: present"
  exit 1
fi
if (( validated == 0 )); then
  error "no Database CR declares vector or vchord"
  exit 1
fi
if (( vector_targets == 0 )); then
  error "no Database CR declares vector"
  exit 1
fi
if (( vchord_targets == 0 )); then
  error "no Database CR declares vchord"
  exit 1
fi
