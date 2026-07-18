#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
VALIDATOR="$SCRIPT_ROOT/scripts/validate-cnpg-extension-versions.sh"
FIXTURE_REPO=$(mktemp -d)
trap 'rm -rf "$FIXTURE_REPO"' EXIT

mkdir -p "$FIXTURE_REPO/kubernetes/apps/database/cloudnative-pg/cluster"
mkdir -p "$FIXTURE_REPO/kubernetes/apps/database/cloudnative-pg/databases"
mkdir -p "$FIXTURE_REPO/bin"

cat >"$FIXTURE_REPO/kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml" <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgres16
spec:
  imageName: ghcr.io/cloudnative-pg/postgresql:18.4-standard-trixie@sha256:test
  postgresql:
    extensions:
      - name: vchord
        image:
          reference: ghcr.io/tensorchord/vchord-scratch:pg18-v1.1.1
      - name: vector
        image:
          reference: ghcr.io/tensorchord/vchord-scratch:pg18-v1.1.1
EOF

cat >"$FIXTURE_REPO/kubernetes/apps/database/cloudnative-pg/databases/memini.yaml" <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: memini
spec:
  cluster:
    name: postgres16
  name: memini
  extensions:
    - name: vchord
      ensure: present
      version: 1.1.1
    - name: vector
      ensure: present
      version: 0.8.5
EOF

cat >"$FIXTURE_REPO/kubernetes/apps/database/cloudnative-pg/databases/crowdsec.yaml" <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: crowdsec
spec:
  cluster:
    name: postgres16
  name: crowdsec
  extensions:
    - name: vector
      version: 0.8.5
EOF

cat >"$FIXTURE_REPO/bin/crane" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 || $1 != export || $2 != --platform || $3 != linux/amd64 || $4 != ghcr.io/cloudnative-pg/postgresql:18.4-standard-trixie@sha256:test || $5 != - ]]; then
  exit 2
fi
printf '%s\n' 'fake image archive stream'
EOF

cat >"$FIXTURE_REPO/bin/tar" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 || $1 != -xOf || $2 != - || $3 != usr/share/postgresql/18/extension/vector.control ]]; then
  exit 2
fi
cat >/dev/null
printf "%s\n" "default_version = '0.8.5'"
EOF

chmod +x "$FIXTURE_REPO/bin/crane" "$FIXTURE_REPO/bin/tar"

run_guard() {
  REPO_ROOT="$FIXTURE_REPO" \
    CRANE_BIN="$FIXTURE_REPO/bin/crane" \
    TAR_BIN="$FIXTURE_REPO/bin/tar" \
    "$VALIDATOR"
}

reset_database_extensions() {
  yq -i '.spec.extensions = [{"name": "vchord", "ensure": "present", "version": "1.1.1"}, {"name": "vector", "ensure": "present", "version": "0.8.5"}]' "$memini"
  yq -i '.spec.extensions = [{"name": "vector", "version": "0.8.5"}]' "$crowdsec"
}

run_guard

memini="$FIXTURE_REPO/kubernetes/apps/database/cloudnative-pg/databases/memini.yaml"
crowdsec="$FIXTURE_REPO/kubernetes/apps/database/cloudnative-pg/databases/crowdsec.yaml"
cluster="$FIXTURE_REPO/kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml"
yq -i '(.spec.extensions[] | select(.name == "vector").version) = "0.8.2"' "$memini"
if output=$(run_guard 2>&1); then
  printf '%s\n' 'expected vector version mismatch to fail' >&2
  exit 1
fi
[[ "$output" == *memini* && "$output" == *vector* && "$output" == *0.8.2* && "$output" == *0.8.5* ]] || {
  printf '%s\n' "unexpected vector diagnostic: $output" >&2
  exit 1
}

yq -i '(.spec.extensions[] | select(.name == "vector").version) = "0.8.5"' "$memini"
yq -i '(.spec.extensions[] | select(.name == "vchord").version) = "1.0.0"' "$memini"
if output=$(run_guard 2>&1); then
  printf '%s\n' 'expected vchord version mismatch to fail' >&2
  exit 1
fi
[[ "$output" == *memini* && "$output" == *vchord* && "$output" == *1.0.0* && "$output" == *1.1.1* ]] || {
  printf '%s\n' "unexpected vchord diagnostic: $output" >&2
  exit 1
}

yq -i '(.spec.extensions[] | select(.name == "vchord").version) = "1.1.1"' "$memini"
yq -i 'del(.spec.extensions[] | select(.name == "vchord").version)' "$memini"
if output=$(run_guard 2>&1); then
  printf '%s\n' 'expected missing vchord version to fail' >&2
  exit 1
fi
[[ "$output" == *memini* && "$output" == *vchord* && "$output" == *missing* && "$output" == *version* ]] || {
  printf '%s\n' "unexpected missing-version diagnostic: $output" >&2
  exit 1
}

reset_database_extensions
yq -i 'del(.spec.extensions[] | select(.name == "vector"))' "$memini"
if output=$(run_guard 2>&1); then
  printf '%s\n' 'expected missing Memini vector declaration to fail' >&2
  exit 1
fi
[[ "$output" == *"memini vector extension must declare ensure: present"* ]] || {
  printf '%s\n' "unexpected missing-Memini-vector diagnostic: $output" >&2
  exit 1
}

reset_database_extensions
yq -i '.spec.extensions += [{"name": "vchord", "ensure": "present", "version": "1.1.1"}]' "$crowdsec"
yq -i 'del(.spec.extensions[] | select(.name == "vchord"))' "$memini"
if output=$(run_guard 2>&1); then
  printf '%s\n' 'expected missing Memini vchord declaration to fail' >&2
  exit 1
fi
[[ "$output" == *"memini vchord extension must declare ensure: present"* ]] || {
  printf '%s\n' "unexpected missing-Memini-vchord diagnostic: $output" >&2
  exit 1
}

reset_database_extensions
yq -i '(.spec.extensions[] | select(.name == "vector").ensure) = "absent"' "$memini"
if output=$(run_guard 2>&1); then
  printf '%s\n' 'expected Memini absent vector to fail' >&2
  exit 1
fi
[[ "$output" == *"memini vector extension must declare ensure: present"* ]] || {
  printf '%s\n' "unexpected absent-Memini-vector diagnostic: $output" >&2
  exit 1
}

reset_database_extensions
yq -i '(.spec.postgresql.extensions[] | select(.name == "vector").image.reference) = "ghcr.io/tensorchord/vchord-scratch:pg18-v0.8.5"' "$cluster"
if output=$(run_guard 2>&1); then
  printf '%s\n' 'expected disagreeing extension image references to fail' >&2
  exit 1
fi
[[ "$output" == *"vchord and vector image references differ"* ]] || {
  printf '%s\n' "unexpected disagreeing-image diagnostic: $output" >&2
  exit 1
}

printf '%s\n' 'CNPG extension version guard regression tests passed'
