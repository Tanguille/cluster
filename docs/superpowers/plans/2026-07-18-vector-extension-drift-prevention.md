# Vector Extension Drift Prevention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CloudNativePG extension versions declarative and prevent a pull request from changing the pinned extension-bearing images without updating the matching database targets.

**Architecture:** CloudNativePG `Database` resources for supported applications declare installed `vector` and `vchord` versions. The reserved `postgres` database remains unmanaged because no user objects depend on `vector` there. A shell guard reads the application declarations, extracts pgvector's control-file version from the exact pinned operand image with `crane`, and verifies the VectorChord tag/version relationship. The existing Image Pull workflow runs the guard and its regression test for Kubernetes pull requests.

**Tech Stack:** Flux, CloudNativePG `Database` CRs, Bash, `yq`, `crane`, GitHub Actions, Renovate JSON5.

---

## File structure

- Create `scripts/validate-cnpg-extension-versions.sh`: validates declared extension targets against the exact pinned OCI images.
- Create `tests/scripts/test-validate-cnpg-extension-versions.sh`: shell regression coverage with a temporary manifest fixture and mocked OCI extraction.
- Modify `.github/workflows/image-pull.yaml`: installs the repository-pinned tools and executes the regression test and guard.
- Modify `kubernetes/apps/database/cloudnative-pg/databases/{memini,crowdsec}.yaml`: pin every supported application extension target.
- Modify `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml`: remove duplicate Renovate annotations already handled by the upstream CNPG manager.
- Modify `.renovaterc.json5`: repair the dashboard manager and narrow the reviewed rules without replacing the upstream preset.

### Task 1: Write the compatibility-guard regression test

**Files:**
- Create: `tests/scripts/test-validate-cnpg-extension-versions.sh`
- Test: `tests/scripts/test-validate-cnpg-extension-versions.sh`

- [ ] **Step 1: Create the failing test script**

```bash
#!/bin/bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VALIDATOR="${REPO_ROOT}/scripts/validate-cnpg-extension-versions.sh"
FIXTURE_ROOT="$(mktemp -d)"

cleanup() {
  rm -rf "${FIXTURE_ROOT}"
}
trap cleanup EXIT

mkdir -p \
  "${FIXTURE_ROOT}/bin" \
  "${FIXTURE_ROOT}/kubernetes/apps/database/cloudnative-pg/cluster" \
  "${FIXTURE_ROOT}/kubernetes/apps/database/cloudnative-pg/databases"

cat >"${FIXTURE_ROOT}/kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml" <<'YAML'
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
spec:
  imageName: ghcr.io/cloudnative-pg/postgresql:18.4-standard-trixie@sha256:test
  postgresql:
    extensions:
      - name: vchord
        image:
          reference: ghcr.io/tensorchord/vchord-scratch:pg18-v1.1.1
YAML

cat >"${FIXTURE_ROOT}/kubernetes/apps/database/cloudnative-pg/databases/memini.yaml" <<'YAML'
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: memini
spec:
  extensions:
    - name: vector
      ensure: present
      version: "0.8.5"
    - name: vchord
      ensure: present
      version: "1.1.1"
YAML

cat >"${FIXTURE_ROOT}/kubernetes/apps/database/cloudnative-pg/databases/crowdsec.yaml" <<'YAML'
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: crowdsec
spec:
  extensions:
    - name: vector
      ensure: present
      version: "0.8.5"
YAML

cat >"${FIXTURE_ROOT}/bin/crane" <<'SH'
#!/bin/bash
set -euo pipefail
[ "$1" = "export" ] || { echo "unexpected crane command: $*" >&2; exit 1; }
printf 'fixture filesystem tar stream\n'
SH

cat >"${FIXTURE_ROOT}/bin/tar" <<'SH'
#!/bin/bash
set -euo pipefail
cat >/dev/null
printf "default_version = '0.8.5'\n"
SH
chmod +x "${FIXTURE_ROOT}/bin/crane" "${FIXTURE_ROOT}/bin/tar"

run_guard() {
  REPO_ROOT="${FIXTURE_ROOT}" \
    CRANE_BIN="${FIXTURE_ROOT}/bin/crane" \
    TAR_BIN="${FIXTURE_ROOT}/bin/tar" \
    bash "${VALIDATOR}"
}

run_guard

yq -i '(.spec.extensions[] | select(.name == "vector").version) = "0.8.2"' \
  "${FIXTURE_ROOT}/kubernetes/apps/database/cloudnative-pg/databases/memini.yaml"
if run_guard; then
  echo "expected a mismatched vector target to fail" >&2
  exit 1
fi

yq -i '(.spec.extensions[] | select(.name == "vector").version) = "0.8.5"' \
  "${FIXTURE_ROOT}/kubernetes/apps/database/cloudnative-pg/databases/memini.yaml"
yq -i '(.spec.extensions[] | select(.name == "vchord").version) = ""' \
  "${FIXTURE_ROOT}/kubernetes/apps/database/cloudnative-pg/databases/memini.yaml"
if run_guard; then
  echo "expected a missing vchord target to fail" >&2
  exit 1
fi

echo "CNPG extension-version guard tests passed"
```

- [ ] **Step 2: Run the test to verify it fails before implementation**

Run: `mise exec -- bash tests/scripts/test-validate-cnpg-extension-versions.sh`

Expected: non-zero exit with `scripts/validate-cnpg-extension-versions.sh: No such file or directory`.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/scripts/test-validate-cnpg-extension-versions.sh
git commit -m "test(database): cover cnpg extension version guard"
```

### Task 2: Implement the pinned-image compatibility guard

**Files:**
- Create: `scripts/validate-cnpg-extension-versions.sh`
- Test: `tests/scripts/test-validate-cnpg-extension-versions.sh`

- [ ] **Step 1: Create the validator**

```bash
#!/bin/bash

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
YQ_BIN="${YQ_BIN:-yq}"
CRANE_BIN="${CRANE_BIN:-crane}"
TAR_BIN="${TAR_BIN:-tar}"
CLUSTER_MANIFEST="${REPO_ROOT}/kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml"
DATABASE_DIRECTORY="${REPO_ROOT}/kubernetes/apps/database/cloudnative-pg/databases"

fail() {
  echo "error: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null || fail "required command not found: $1"
}

validate_targets() {
  local extension="$1"
  local expected_version="$2"
  local targets
  local database
  local declared_version
  local found=0

  targets="$(EXTENSION="${extension}" "${YQ_BIN}" -r '
    select(.kind == "Database") |
    .metadata.name as $database |
    .spec.extensions[]? |
    select(.name == strenv(EXTENSION)) |
    "\($database):\(.version // \"\")"
  ' "${DATABASE_DIRECTORY}"/*.yaml)"

  [ -n "${targets}" ] || fail "no Database CR declares ${extension}"

  while IFS=: read -r database declared_version; do
    found=1
    [ -n "${declared_version}" ] || fail "${database} declares ${extension} without spec.extensions[].version"
    [ "${declared_version}" = "${expected_version}" ] || fail "${database} declares ${extension} ${declared_version}, expected ${expected_version}"
    echo "${database}: ${extension} ${declared_version}"
  done <<<"${targets}"

  [ "${found}" -eq 1 ] || fail "no Database CR declares ${extension}"
}

require_command "${YQ_BIN}"
require_command "${CRANE_BIN}"
require_command "${TAR_BIN}"
[ -f "${CLUSTER_MANIFEST}" ] || fail "cluster manifest not found: ${CLUSTER_MANIFEST}"

operand_image="$("${YQ_BIN}" -r '.spec.imageName' "${CLUSTER_MANIFEST}")"
[ -n "${operand_image}" ] && [ "${operand_image}" != "null" ] || fail "missing spec.imageName"

vector_version="$(
  "${CRANE_BIN}" export --platform linux/amd64 "${operand_image}" - |
    "${TAR_BIN}" -xOf - usr/share/postgresql/18/extension/vector.control |
    awk -F= '
      $1 ~ /^[[:space:]]*default_version[[:space:]]*$/ { print $2; found = 1 }
      END { if (!found) exit 1 }
    '
)"
vector_version="${vector_version//[[:space:]']/}"
[ -n "${vector_version}" ] || fail "unable to read pgvector default_version from ${operand_image}"

vchord_image="$("${YQ_BIN}" -r '
  .spec.postgresql.extensions[] |
  select(.name == "vchord") |
  .image.reference
' "${CLUSTER_MANIFEST}")"
[ -n "${vchord_image}" ] && [ "${vchord_image}" != "null" ] || fail "missing vchord extension image"
vchord_tag="${vchord_image%@*}"
vchord_tag="${vchord_tag##*:}"
[[ "${vchord_tag}" =~ ^pg18-v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || fail "unsupported vchord tag: ${vchord_tag}"
vchord_version="${BASH_REMATCH[1]}"

validate_targets "vector" "${vector_version}"
validate_targets "vchord" "${vchord_version}"
```

- [ ] **Step 2: Make the validator executable**

Run: `chmod +x scripts/validate-cnpg-extension-versions.sh tests/scripts/test-validate-cnpg-extension-versions.sh`

- [ ] **Step 3: Run the regression test and shellcheck**

Run: `mise exec -- bash tests/scripts/test-validate-cnpg-extension-versions.sh && mise exec -- shellcheck scripts/validate-cnpg-extension-versions.sh tests/scripts/test-validate-cnpg-extension-versions.sh`

Expected: `CNPG extension-version guard tests passed` and no ShellCheck output.

- [ ] **Step 4: Run the guard against repository manifests**

Run: `mise exec -- bash scripts/validate-cnpg-extension-versions.sh`

Expected: non-zero exit until Task 4 adds all extension target versions.

- [ ] **Step 5: Commit the implementation**

```bash
git add scripts/validate-cnpg-extension-versions.sh tests/scripts/test-validate-cnpg-extension-versions.sh
git commit -m "test(database): validate cnpg extension versions"
```

### Task 3: Run the guard in pull-request CI

**Files:**
- Modify: `.github/workflows/image-pull.yaml:56-104`
- Test: `.github/workflows/image-pull.yaml`

- [ ] **Step 1: Add the `extension-versions` job after `flate`**

Insert this job before `pull`:

```yaml
  extension-versions:
    if: ${{ needs.filter.outputs.changed-files != '[]' }}
    needs: filter
    name: Image Pull - CNPG Extension Versions
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          persist-credentials: false

      - name: Setup Mise
        uses: jdx/mise-action@e6a8b3978addb5a52f2b4cd9d91eafa7f0ab959d # v4.2.0
        with:
          experimental: true
          install_args: --locked

      - name: Test CNPG extension-version guard
        run: bash tests/scripts/test-validate-cnpg-extension-versions.sh

      - name: Validate CNPG extension versions
        run: bash scripts/validate-cnpg-extension-versions.sh
```

- [ ] **Step 2: Include the job in the terminal status check**

Change the `success` job dependencies to:

```yaml
    needs: [flate, extension-versions, pull]
```

- [ ] **Step 3: Validate workflow syntax and the local guard**

Run: `mise exec -- bash tests/scripts/test-validate-cnpg-extension-versions.sh && mise exec -- bash scripts/validate-cnpg-extension-versions.sh`

Expected: the test passes; the guard remains red until Task 4.

- [ ] **Step 4: Commit the workflow**

```bash
git add .github/workflows/image-pull.yaml
git commit -m "ci(database): validate cnpg extension targets"
```

### Task 4: Declare supported application extension versions in CloudNativePG

**Files:**
- Modify: `kubernetes/apps/database/cloudnative-pg/databases/memini.yaml:15-20`
- Modify: `kubernetes/apps/database/cloudnative-pg/databases/crowdsec.yaml:15-18`
- Test: `scripts/validate-cnpg-extension-versions.sh`

The reserved `postgres` database remains unmanaged: no user objects depend on
`vector` there, and no migration Job is required. Supported application
`Database` CRs are the remediation path.

- [ ] **Step 1: Pin every supported application extension target**

In `memini.yaml`, make the extension list exactly:

```yaml
  extensions:
    - name: vchord
      ensure: present
      version: "1.1.1"
    - name: vector
      ensure: present
      version: "0.8.5"
```

In `crowdsec.yaml`, make the extension list exactly:

```yaml
  extensions:
    - name: vector
      ensure: present
      version: "0.8.5"
```

- [ ] **Step 2: Run the guard, render validation, and shellcheck**

Run: `mise exec -- bash scripts/validate-cnpg-extension-versions.sh && bash .agents/skills/pr-review/scripts/validate-pr.sh`

Expected: every declared extension is printed at its expected target and the GitOps validation exits zero.

- [ ] **Step 3: Commit the declarative application targets**

```bash
git add kubernetes/apps/database/cloudnative-pg/databases
git commit -m "fix(database): pin supported extension versions"
```

### Task 5: Remove redundant CNPG extraction and simplify proven Renovate gaps

**Files:**
- Modify: `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml:141-149`
- Modify: `.renovaterc.json5:8,21-32,68-75,98-159`
- Test: `.renovaterc.json5`

- [ ] **Step 1: Remove the two redundant CNPG Renovate comments**

Delete only these comments above the `vchord` and `vector` image references:

```yaml
# renovate: datasource=docker depName=ghcr.io/tensorchord/vchord-scratch
```

Keep both `reference: ghcr.io/tensorchord/vchord-scratch:pg18-v1.1.1` lines unchanged. The pinned upstream CNPG manager recognizes `spec.imageName` and extension `reference` fields directly.

- [ ] **Step 2: Repair the recursive dashboard URL manager**

Replace its first `matchStrings` entry with the full URL matcher, preserving the second expression and replacement template:

```json5
"url:\\s+https://raw\\.githubusercontent\\.com/(?<depName>[^/\\s]+/[^/\\s]+)/[^\\s]+"
```

- [ ] **Step 3: Narrow blind spots and avoid suppressing independent image updates**

Make these exact Renovate changes:

```json5
ignorePaths: ["**/*.sops.*", "kubernetes/apps/**/resources/**"],
```

```json5
matchPackageNames: ["/^ghcr\\.io\\/home-operations\\//"],
```

Delete `minimumGroupSize: 2` from only the `toolhive-mcp` and `nginx sidecar images` rules. Retain it for Actions Runner Controller, Flux Operator, Rook-Ceph, and ToolHive operator/CRDs, and add a `must release together` comment to each of those retained thresholds.

Use these exact comments immediately above their retained thresholds:

```json5
// Controller and scale-set chart must release together.
// Flux instance and operator must release together.
// Rook operator and cluster chart must release together.
// ToolHive operator and CRDs must release together.
```

- [ ] **Step 4: Validate Renovate configuration syntax**

Run: `mise exec -- oxfmt --check .renovaterc.json5`

Expected: exit zero with no format diff; Renovate's hosted run remains the semantic validation of its preset-resolved configuration.

- [ ] **Step 5: Commit the Renovate changes**

```bash
git add .renovaterc.json5 kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml
git commit -m "fix(renovate): prevent cnpg extension update drift"
```

### Task 6: Verify the complete PR branch

**Files:**
- Verify: all files listed above

- [ ] **Step 1: Review the branch diff and working tree**

Run: `git status --short && git diff origin/main...HEAD --check && git diff --stat origin/main...HEAD`

Expected: clean worktree, no whitespace errors, and changes limited to the compatibility guard, its test, CI, CNPG manifests, Renovate configuration, and design/plan documentation.

- [ ] **Step 2: Run the narrow checks**

Run: `mise exec -- bash tests/scripts/test-validate-cnpg-extension-versions.sh && mise exec -- bash scripts/validate-cnpg-extension-versions.sh && mise exec -- shellcheck scripts/validate-cnpg-extension-versions.sh tests/scripts/test-validate-cnpg-extension-versions.sh`

Expected: all commands exit zero.

- [ ] **Step 3: Run GitOps validation**

Run: `bash .agents/skills/pr-review/scripts/validate-pr.sh`

Expected: `flate test all passed` and `shellcheck passed`.

- [ ] **Step 4: Inspect the rendered Database resources**

Run: `mise exec -- kustomize build kubernetes/apps/database/cloudnative-pg/databases`

Expected: rendered `Database` resources for `memini` and `crowdsec` retain reclaim policies; `vector` is explicitly targeted at `0.8.5` in both resources, and `vchord` is explicitly targeted at `1.1.1` in `memini`. The reserved `postgres` database is not rendered or managed.

- [ ] **Step 5: Request a GitOps-focused review before pushing**

Run: follow `.agents/skills/pr-review/SKILL.md` for a local-diff review.

Expected: review findings are either fixed or explicitly documented before creating the pull request.

### Task 7: Verify the GitOps reconciliation after Flux applies it

**Files:**
- Verify: `kubernetes/apps/database/cloudnative-pg/databases/{memini,crowdsec}.yaml`

- [ ] **Step 1: Identify the CNPG primary without changing cluster state**

Run:

```bash
primary="$(mise exec -- kubectl get pods -n database \
  -l cnpg.io/cluster=postgres16 \
  -l cnpg.io/instanceRole=primary \
  -o jsonpath='{.items[0].metadata.name}')"
printf '%s\n' "${primary}"
```

Expected: one primary pod name such as `postgres16-2`.

- [ ] **Step 2: Inventory installed and available vector extensions in every connectable database**

Run:

```bash
primary="$(mise exec -- kubectl get pods -n database \
  -l cnpg.io/cluster=postgres16 \
  -l cnpg.io/instanceRole=primary \
  -o jsonpath='{.items[0].metadata.name}')"
mise exec -- kubectl exec -n database "${primary}" -c postgres -- \
  psql -U postgres -Atc \
  "SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY datname" |
while IFS= read -r database; do
  mise exec -- kubectl exec -n database "${primary}" -c postgres -- \
    psql -U postgres -d "${database}" -P pager=off -c \
    "SELECT current_database(), extname, extversion
     FROM pg_extension
     WHERE extname IN ('vector', 'vchord')
     ORDER BY extname;
     SELECT name, default_version, installed_version
     FROM pg_available_extensions
     WHERE name IN ('vector', 'vchord')
     ORDER BY name;"
done
```

Expected: `memini` and `crowdsec` report `vector` installed at `0.8.5`; `memini` reports `vchord` at `1.1.1`. The reserved `postgres` database remains unmanaged. Any extension found in another supported application database must be declared in its application `Database` CR before its version is managed.
