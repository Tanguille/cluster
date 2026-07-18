#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT=$(git rev-parse --show-toplevel)
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
      version: 1.1.1
    - name: vector
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

if [[ ${1:-} != export ]]; then
  exit 2
fi
printf '%s\n' 'fake image archive stream'
EOF

cat >"$FIXTURE_REPO/bin/tar" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
printf "%s\n" "default_version = '0.8.5'"
EOF

chmod +x "$FIXTURE_REPO/bin/crane" "$FIXTURE_REPO/bin/tar"

run_guard() {
  REPO_ROOT="$FIXTURE_REPO" \
    CRANE_BIN="$FIXTURE_REPO/bin/crane" \
    TAR_BIN="$FIXTURE_REPO/bin/tar" \
    "$SCRIPT_ROOT/scripts/validate-cnpg-extension-versions.sh"
}

run_guard

memini="$FIXTURE_REPO/kubernetes/apps/database/cloudnative-pg/databases/memini.yaml"
yq -i '(.spec.extensions[] | select(.name == "vector").version) = "0.8.2"' "$memini"
if run_guard; then
  printf '%s\n' 'expected vector version mismatch to fail' >&2
  exit 1
fi

yq -i '(.spec.extensions[] | select(.name == "vector").version) = "0.8.5"' "$memini"
yq -i 'del(.spec.extensions[] | select(.name == "vchord").version)' "$memini"
if run_guard; then
  printf '%s\n' 'expected blank vchord version to fail' >&2
  exit 1
fi

printf '%s\n' 'CNPG extension version guard regression tests passed'
