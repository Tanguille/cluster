#!/usr/bin/env bash
# Focused runtime contract checks for the Paseo child image.
#
# Usage: scripts/paseo-smoke.sh <local-image-ref>
#
# This is the single durable verification affordance for the image: agent
# binaries survive a fresh mounted HOME, Hermes ACP answers initialize without
# network access, seeding never overwrites existing state, and the daemon fails
# closed on a missing password while still authenticating real control requests.
# It asserts the production URLs are what seed.sh uses; it never rewrites them
# in the implementation. Requires bash, docker, git and python3 (JSON parsing).
# shellcheck disable=SC2016  # container snippets are single-quoted on purpose
set -euo pipefail

IMAGE="${1:-}"
if [[ -z "${IMAGE}" ]]; then
    echo "usage: $0 <local-image-ref>" >&2
    exit 2
fi
command -v python3 >/dev/null || {
    echo "python3 is required" >&2
    exit 2
}

readonly PLATFORM="linux/amd64"
readonly RUN_AS="1000:1000"
readonly CLUSTER_URL="https://github.com/Tanguille/cluster.git"
readonly MESH_URL="https://github.com/Tanguille/mesh-organiser.git"

SMOKE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/paseo-smoke.XXXXXX")"
chmod 0777 "${SMOKE_ROOT}"
CONTAINER="paseo-smoke-$$"
CONTAINER_ROOT="${CONTAINER}-root"
CONTAINER_ACP="${CONTAINER}-acp"

PASS=0
ok() {
    PASS=$((PASS + 1))
    printf 'ok %d - %s\n' "${PASS}" "$1"
}
die() {
    printf 'not ok - %s\n' "$1" >&2
    exit 1
}
assert_eq() {
    [[ "$2" == "$3" ]] || die "$1 (expected '$3', got '$2')"
    ok "$1"
}
assert_contains() {
    [[ "$2" == *"$3"* ]] || die "$1 (missing '$3' in '$2')"
    ok "$1"
}
assert_absent() {
    local path="$1" label="${2:-$1}"
    fixture 'test ! -e "$1" && test ! -L "$1"' "/smoke/${path#"${SMOKE_ROOT}/"}" ||
        die "expected absent: ${label}"
    ok "absent: ${label}"
}

cleanup() {
    docker rm -f "${CONTAINER}" "${CONTAINER_ROOT}" "${CONTAINER_ACP}" >/dev/null 2>&1 || true
    # UID 1000 owns fixture contents even when the host caller has another UID.
    docker run --rm --platform "${PLATFORM}" --user "${RUN_AS}" \
        -v "${SMOKE_ROOT}:/smoke" --entrypoint /bin/sh "${IMAGE}" \
        -c 'for entry in /smoke/* /smoke/.[!.]* /smoke/..?*; do [ -e "$entry" ] || [ -L "$entry" ] || continue; rm -rf -- "$entry"; done' \
        >/dev/null 2>&1 || true
    rmdir -- "${SMOKE_ROOT}" 2>/dev/null || true
}
trap cleanup EXIT

# Run a shell snippet as UID 1000 with disposable, 1000-owned mounts.
img() {
    docker run --rm --platform "${PLATFORM}" --user "${RUN_AS}" \
        --tmpfs /home/paseo:rw,uid=1000,gid=1000,mode=0755 \
        --tmpfs /workspace:rw,uid=1000,gid=1000,mode=0755 \
        --entrypoint /bin/sh "${IMAGE}" -c "$1"
}

# Keep fixture ownership independent of the invoking host UID.
fixture() {
    local script="$1"
    shift
    docker run --rm --platform "${PLATFORM}" --user "${RUN_AS}" \
        -v "${SMOKE_ROOT}:/smoke" --entrypoint /bin/sh "${IMAGE}" \
        -c 'git config --global --add safe.directory "*"; script="$1"; shift; exec /bin/sh -c "$script" sh "$@"' \
        fixture "${script}" "$@"
}

assert_file() {
    local in_container="/smoke/${1#"${SMOKE_ROOT}/"}"
    fixture 'test -f "$1"' "${in_container}" || {
        fixture 'ls -ld /smoke "$1" "$(dirname "$1")" 2>&1 || true' "${in_container}" || true
        die "expected file $1"
    }
    ok "exists: $1"
}
fixture_cat() { fixture 'cat "$1"' "/smoke/${1#"${SMOKE_ROOT}/"}"; }

echo "== binaries, versions and resolution outside HOME =="
versions="$(img '
  set -eu
  # The upstream entrypoint and paseo-seed create these on a real mount; the
  # disposable tmpfs starts empty, so create them the same way before probing.
  mkdir -p "$HOME/.paseo" "$HOME/.claude" "$HOME/.codex" "$HOME/.config" \
    "$HOME/.local/share" "$HOME/.local/state" "$HOME/.cache" "$HOME/.hermes"
  for b in opencode claude codex gh hermes; do
    p="$(command -v "$b")" || { echo "MISSING $b" >&2; exit 1; }
    r="$(readlink -f "$p")"
    case "$r" in
      /home/paseo/*) echo "INSIDE_HOME $b $r" >&2; exit 1 ;;
    esac
    echo "$b=$r"
  done
  opencode_v="$(opencode --version 2>&1)" || exit 1
  claude_v="$(claude --version 2>&1)" || exit 1
  codex_v="$(codex --version 2>&1)" || exit 1
  gh_v="$(gh --version 2>&1)" || exit 1
  hermes --help >/dev/null 2>&1 || exit 1
  printf "opencode-version=%s\\nclaude-version=%s\\ncodex-version=%s\\ngh-version=%s\\nhermes-help=ok\\n" \
    "$opencode_v" "$claude_v" "$codex_v" "$gh_v"
  echo "node-version=$(node --version)"
  echo "node-path=$(command -v node)"
')" || die "binary inventory failed"
for binary in opencode claude codex gh hermes; do
    resolved="$(sed -n "s/^${binary}=//p" <<<"${versions}")"
    case "${resolved}" in
    /opt/* | /usr/*) ok "${binary} resolves outside HOME (${resolved})" ;;
    *) die "${binary} did not resolve outside HOME (${resolved:-missing})" ;;
    esac
done
assert_contains "opencode version is pinned" "${versions}" "1.18.35"
assert_contains "claude version is pinned" "${versions}" "2.1.310"
assert_contains "codex version is pinned" "${versions}" "0.164.0"
assert_contains "gh version is pinned" "${versions}" "gh version 2.103.2"
assert_contains "hermes --help works" "${versions}" "hermes-help=ok"
assert_contains "Paseo Node is unchanged" "${versions}" "node-version=v22.23.4"
assert_contains "Paseo Node path is unchanged" "${versions}" "node-path=/usr/local/bin/node"

echo "== Hermes source, sealed venv and SQLite =="
hermes_env="$(img '
  set -eu
  python3 -c "import sqlite3, sys; print(\"python=\" + sys.version.split()[0]); print(\"sqlite=\" + sqlite3.sqlite_version)"
  /opt/hermes/.venv/bin/python -c "import acp, sys; print(\"venv-python=\" + sys.executable); print(\"acp=\" + acp.__name__)"
  test -r /opt/hermes/pyproject.toml && echo "source=readable"
  test -x /opt/hermes/.venv/bin/hermes && echo "venv-hermes=executable"
')" || die "Hermes environment inventory failed"
assert_contains "PM Python 3.14 is provisioned" "${hermes_env}" "python=3.14."
sqlite_version="$(sed -n 's/^sqlite=//p' <<<"${hermes_env}")"
[[ -n "${sqlite_version}" ]] || die "could not read SQLite version"
if [[ "$(printf '%s\n3.51.3\n' "${sqlite_version}" | sort -V | head -n1)" == "3.51.3" ]]; then
    ok "SQLite ${sqlite_version} >= 3.51.3"
else
    die "SQLite ${sqlite_version} is older than 3.51.3"
fi
assert_contains "ACP extra imports in the sealed venv" "${hermes_env}" "acp=acp"
assert_contains "Hermes source stays available" "${hermes_env}" "source=readable"
assert_contains "sealed venv exposes hermes" "${hermes_env}" "venv-hermes=executable"

echo "== Hermes ACP initialize with external networking disabled =="
acp_out="${SMOKE_ROOT}/acp.jsonl"
acp_err="${SMOKE_ROOT}/acp.err"
# stdin must stay open while the agent boots (plugin discovery takes seconds);
# closing it immediately makes the agent exit before answering.
PASEO_ACP_CONTAINER="${CONTAINER_ACP}" python3 - "${IMAGE}" "${acp_out}" "${acp_err}" <<'PY' || die "hermes acp did not answer within 180s"
import json
import os
import selectors
import subprocess
import sys
import time

image, out_path, err_path = sys.argv[1:4]
request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": 1,
        "clientCapabilities": {
            "fs": {"readTextFile": True, "writeTextFile": True},
            "terminal": True,
        },
    },
}
command = [
    "docker", "run", "--name", os.environ.get("PASEO_ACP_CONTAINER", "paseo-smoke-acp"), "-i", "--init", "--platform", "linux/amd64",
    "--user", "1000:1000", "--network", "none",
    "--tmpfs", "/home/paseo:rw,uid=1000,gid=1000,mode=0755",
    "--tmpfs", "/workspace:rw,uid=1000,gid=1000,mode=0755",
    "--entrypoint", "/bin/sh", image, "-c", "exec hermes acp",
]
with open(err_path, "wb") as err:
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
    proc.stdin.write((json.dumps(request) + "\n").encode())
    proc.stdin.flush()
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 180
    buffer = b""
    answered = False
    with open(out_path, "wb") as out:
        while time.monotonic() < deadline:
            if not selector.select(timeout=1):
                if proc.poll() is not None:
                    break
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            out.write(chunk)
            out.flush()
            buffer += chunk
            for line in buffer.splitlines():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") == 1:
                    answered = True
            if answered:
                break
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    finally:
        subprocess.run(["docker", "rm", "-f", os.environ.get("PASEO_ACP_CONTAINER", "paseo-smoke-acp")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
sys.exit(0 if answered else 1)
PY
python3 - "${acp_out}" <<'PY' || die "Hermes ACP initialize did not return a successful response"
import json
import sys

messages = []
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        messages.append(json.loads(line))
    except json.JSONDecodeError:
        continue

for message in messages:
    if message.get("id") == 1:
        if "error" in message:
            raise SystemExit(f"ACP initialize returned an error: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise SystemExit("ACP initialize response has no result object")
        if "protocolVersion" not in result:
            raise SystemExit("ACP initialize result has no protocolVersion")
        if not isinstance(result.get("agentCapabilities"), dict):
            raise SystemExit("ACP initialize result has no agentCapabilities")
        raise SystemExit(0)
raise SystemExit("no JSON-RPC response with id 1")
PY
ok "hermes acp answered initialize with agentCapabilities"
if grep -q "install out of sync" "${acp_err}"; then
    tail -n 20 "${acp_err}" >&2
    die "the sealed Hermes install reports PM drift"
fi
ok "sealed Hermes install reports no PM drift"

echo "== seed fixtures =="
remotes="${SMOKE_ROOT}/remotes"
bin_dir="${SMOKE_ROOT}/bin"
mkdir -p "${remotes}" "${bin_dir}"
chmod 0777 "${remotes}" "${bin_dir}"

make_remote() {
    local name="$1" content="$2"
    fixture '
    name="$1" content="$2"; src="/smoke/src-${name}"
    git init -q --initial-branch=main "${src}"
    printf "%s\\n" "${content}" >"${src}/README.md"
    git -C "${src}" -c user.name=smoke -c user.email=smoke@example.invalid add README.md
    git -C "${src}" -c user.name=smoke -c user.email=smoke@example.invalid commit -qm "fixture ${name}"
    git clone -q --bare "${src}" "/smoke/remotes/${name}.git"
  ' "${name}" "${content}"
}
make_remote cluster "cluster fixture"
make_remote mesh-organiser "mesh fixture"

# PATH-scoped Git wrapper: maps only the two approved URLs to local fixtures and
# can simulate a clone failure. The implementation's URLs are never changed.
cat >"${bin_dir}/git" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
args=("$@")
for i in "${!args[@]}"; do
  case "${args[$i]}" in
    "${SMOKE_CLUSTER_URL:?}") args[$i]="${SMOKE_CLUSTER_REMOTE:?}" ;;
    "${SMOKE_MESH_URL:?}") args[$i]="${SMOKE_MESH_REMOTE:?}" ;;
  esac
done
if [[ -n "${SMOKE_FAIL_REMOTE:-}" && "${args[*]}" == *"${SMOKE_FAIL_REMOTE}"* ]]; then
  echo "git wrapper: simulated clone failure for ${SMOKE_FAIL_REMOTE}" >&2
  exit 1
fi
/usr/bin/git "${args[@]}"
rc=$?
if [[ ${rc} -eq 0 && -n "${SMOKE_RACE_AFTER_CLONE:-}" && "${args[*]}" == *cluster.git* ]]; then
  mkdir -p "${SMOKE_RACE_AFTER_CLONE}"
  printf 'concurrent writer\n' >"${SMOKE_RACE_AFTER_CLONE}/race-marker"
fi
exit "${rc}"
WRAPPER
chmod 0755 "${bin_dir}/git"
cat >"${bin_dir}/mv" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
args=("$@")
destination="${args[${#args[@]}-1]}"
if [[ -n "${SMOKE_RACE_DEST:-}" && "${destination}" == "${SMOKE_RACE_DEST}" ]]; then
  if [[ "${SMOKE_RACE_SYMLINK:-}" == 1 ]]; then
    ln -s /smoke/nonexistent-target "${SMOKE_RACE_DEST}"
  else
    mkdir -p "${SMOKE_RACE_DEST}"
    printf 'concurrent writer\n' >"${SMOKE_RACE_DEST}/race-marker"
  fi
fi
exec /usr/bin/mv "$@"
WRAPPER
chmod 0755 "${bin_dir}/mv"

# All bind-mounted inputs and state must be writable by the image's runtime UID.
# This also makes fixture repositories safe for Git's ownership checks.
prepare_mount() {
    mkdir -p "$@"
    chmod 0777 "$@"
}

run_seed() {
    local home="$1" ws="$2"
    docker run --rm --platform "${PLATFORM}" --user "${RUN_AS}" \
        -e HOME=/home/paseo -e PASEO_HOME=/home/paseo/.paseo \
        -e SMOKE_CLUSTER_URL="${CLUSTER_URL}" \
        -e SMOKE_CLUSTER_REMOTE=/smoke/remotes/cluster.git \
        -e SMOKE_MESH_URL="${MESH_URL}" \
        -e SMOKE_MESH_REMOTE=/smoke/remotes/mesh-organiser.git \
        -e SMOKE_FAIL_REMOTE="${SMOKE_FAIL_REMOTE:-}" \
        -e SMOKE_RACE_DEST="${SMOKE_RACE_DEST:-}" \
        -e SMOKE_RACE_SYMLINK="${SMOKE_RACE_SYMLINK:-}" \
        -e SMOKE_RACE_AFTER_CLONE="${SMOKE_RACE_AFTER_CLONE:-}" \
        -v "${home}:/home/paseo" -v "${ws}:/workspace" \
        -v "${bin_dir}:/smoke/bin:ro" -v "${remotes}:/smoke/remotes:ro" \
        --entrypoint /bin/sh "${IMAGE}" -c 'git config --global --add safe.directory "*"; PATH=/smoke/bin:$PATH exec /usr/local/bin/paseo-seed'
}

ws1="${SMOKE_ROOT}/ws1"
home1="${SMOKE_ROOT}/home1"
prepare_mount "${ws1}" "${home1}"
SMOKE_FAIL_REMOTE="" run_seed "${home1}" "${ws1}" || die "first seed run failed"
assert_file "${ws1}/cluster/.git/HEAD"
assert_file "${ws1}/mesh-organiser/.git/HEAD"
assert_eq "cluster fixture checked out on main" \
    "$(fixture 'git -C /smoke/ws1/cluster rev-parse --abbrev-ref HEAD')" "main"
assert_eq "mesh-organiser fixture checked out on main" \
    "$(fixture 'git -C /smoke/ws1/mesh-organiser rev-parse --abbrev-ref HEAD')" "main"
assert_eq "cluster content matches remote" "$(fixture_cat "${ws1}/cluster/README.md")" "cluster fixture"
assert_file "${home1}/.paseo/config.json"
config_json="$(fixture_cat "${home1}/.paseo/config.json")"
python3 -c 'import json,sys; c=json.load(sys.stdin); assert c.get("version")==1,c; assert c.get("agents",{}).get("providers",{}).get("hermes")=={"extends":"acp","label":"Hermes","command":["hermes","acp"]},c' \
    <<<"${config_json}" || die "default config does not match the contract"
ok "default config has the version-1 Hermes ACP provider tuple"
assert_absent "${ws1}/rsume"

echo "== seed rerun preserves dirty, untracked and unrelated state =="
cluster_head_before="$(fixture 'git -C /smoke/ws1/cluster rev-parse HEAD')"
mesh_head_before="$(fixture 'git -C /smoke/ws1/mesh-organiser rev-parse HEAD')"
fixture '
  printf "local edit\\n" > /smoke/ws1/cluster/README.md
  printf "untracked\\n" > /smoke/ws1/cluster/untracked.txt
  mkdir -p /smoke/ws1/plain-dir
  printf "plain\\n" > /smoke/ws1/plain-dir/file.txt
  ln -s /nonexistent/target /smoke/ws1/dangling-link
  printf "{\"version\":1,\"custom\":true}\n" > /smoke/home1/.paseo/config.json
'

SMOKE_FAIL_REMOTE="" run_seed "${home1}" "${ws1}" || die "second seed run failed"
assert_eq "cluster HEAD unchanged on rerun" \
    "$(fixture 'git -C /smoke/ws1/cluster rev-parse HEAD')" "${cluster_head_before}"
assert_eq "mesh-organiser HEAD unchanged on rerun" \
    "$(fixture 'git -C /smoke/ws1/mesh-organiser rev-parse HEAD')" "${mesh_head_before}"
assert_eq "dirty tracked edit preserved" "$(fixture_cat "${ws1}/cluster/README.md")" "local edit"
assert_eq "untracked file preserved" "$(fixture_cat "${ws1}/cluster/untracked.txt")" "untracked"
assert_eq "plain directory preserved" "$(fixture_cat "${ws1}/plain-dir/file.txt")" "plain"
fixture 'test -L /smoke/ws1/dangling-link && test ! -e /smoke/ws1/dangling-link' ||
    die "dangling symlink was replaced or resolved"
ok "dangling symlink preserved"
assert_eq "existing config preserved" "$(fixture_cat "${home1}/.paseo/config.json")" '{"version":1,"custom":true}'
assert_absent "${ws1}/rsume"

echo "== seed skips plain directories and symlinked destinations =="
ws_skip="${SMOKE_ROOT}/ws-skip"
home_skip="${SMOKE_ROOT}/home-skip"
prepare_mount "${ws_skip}" "${home_skip}"
fixture 'mkdir -p /smoke/ws-skip/cluster /smoke/home-skip; printf "keep me\\n" > /smoke/ws-skip/cluster/keep.txt; ln -s /nonexistent/elsewhere /smoke/ws-skip/mesh-organiser'
SMOKE_FAIL_REMOTE="" run_seed "${home_skip}" "${ws_skip}" || die "seed failed on non-Git destinations"
assert_eq "plain directory destination untouched" "$(fixture_cat "${ws_skip}/cluster/keep.txt")" "keep me"
assert_absent "${ws_skip}/cluster/.git"
fixture 'test -L /smoke/ws-skip/mesh-organiser && test ! -e /smoke/ws-skip/mesh-organiser' ||
    die "symlinked destination was replaced"
ok "symlinked destination untouched"
assert_absent "${ws_skip}/rsume"

echo "== failed clone leaves no partial checkout and retries cleanly =="
ws_fail="${SMOKE_ROOT}/ws-fail"
home_fail="${SMOKE_ROOT}/home-fail"
prepare_mount "${ws_fail}" "${home_fail}"
if SMOKE_FAIL_REMOTE="cluster.git" run_seed "${home_fail}" "${ws_fail}" >/dev/null 2>&1; then
    die "seed succeeded although the clone failed"
fi
ok "failed clone makes seed exit nonzero"
assert_absent "${ws_fail}/cluster"
if fixture 'find /smoke/ws-fail -maxdepth 1 -name ".cluster.seed.*" | grep -q .'; then
    die "seed left a temporary clone directory behind"
fi
ok "no temporary clone directory left behind"
SMOKE_FAIL_REMOTE="" run_seed "${home_fail}" "${ws_fail}" || die "retry after failed clone failed"
assert_file "${ws_fail}/cluster/.git/HEAD"
assert_absent "${ws_fail}/rsume"

echo "== concurrent destination created during clone is preserved and cleaned =="
ws_race_clone="${SMOKE_ROOT}/ws-race-clone"
home_race_clone="${SMOKE_ROOT}/home-race-clone"
prepare_mount "${ws_race_clone}" "${home_race_clone}"
SMOKE_FAIL_REMOTE="" SMOKE_RACE_AFTER_CLONE=/workspace/cluster \
    run_seed "${home_race_clone}" "${ws_race_clone}" ||
    die "seed failed when destination appeared during clone"
assert_eq "destination created during clone is preserved" \
    "$(fixture_cat "${ws_race_clone}/cluster/race-marker")" "concurrent writer"
assert_absent "${ws_race_clone}/cluster/.git" "racing clone is not published"
if fixture 'find /smoke/ws-race-clone -maxdepth 1 -name ".cluster.seed.*" | grep -q .'; then
    die "temporary clone leaked after concurrent destination appeared"
fi
ok "temporary clone cleaned after early race return"

echo "== no-clobber publication preserves concurrent directory =="
ws_race_publish="${SMOKE_ROOT}/ws-race-publish"
home_race_publish="${SMOKE_ROOT}/home-race-publish"
prepare_mount "${ws_race_publish}" "${home_race_publish}"
SMOKE_FAIL_REMOTE="" SMOKE_RACE_DEST=/workspace/cluster \
    run_seed "${home_race_publish}" "${ws_race_publish}" ||
    die "seed failed when destination appeared at publication"
assert_eq "destination appearing at publication is preserved" \
    "$(fixture_cat "${ws_race_publish}/cluster/race-marker")" "concurrent writer"
assert_absent "${ws_race_publish}/cluster/.git" "losing clone was not published"
if fixture 'find /smoke/ws-race-publish -maxdepth 1 -name ".cluster.seed.*" | grep -q .'; then
    die "temporary clone leaked after no-clobber publication"
fi
ok "temporary clone cleaned after no-clobber publication"

ws_race_link="${SMOKE_ROOT}/ws-race-link"
home_race_link="${SMOKE_ROOT}/home-race-link"
prepare_mount "${ws_race_link}" "${home_race_link}"
SMOKE_FAIL_REMOTE="" SMOKE_RACE_DEST=/workspace/cluster SMOKE_RACE_SYMLINK=1 \
    run_seed "${home_race_link}" "${ws_race_link}" ||
    die "seed failed when dangling symlink appeared at publication"
fixture 'test -L /smoke/ws-race-link/cluster && test ! -e /smoke/ws-race-link/cluster' ||
    die "publication replaced concurrent dangling symlink"
ok "dangling symlink appearing at publication is preserved"
assert_absent "${ws_race_link}/cluster/.git" "clone did not replace concurrent dangling symlink"

echo "== daemon: fail closed, health, host validation, control auth =="
PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
[[ -n "${PW}" ]] || die "could not generate a test password"

if docker run --rm --platform "${PLATFORM}" --user "${RUN_AS}" \
    --tmpfs /home/paseo:rw,uid=1000,gid=1000,mode=0755 \
    --tmpfs /workspace:rw,uid=1000,gid=1000,mode=0755 \
    "${IMAGE}" >"${SMOKE_ROOT}/nopw.out" 2>"${SMOKE_ROOT}/nopw.err"; then
    die "container started without PASEO_PASSWORD"
fi
ok "missing PASEO_PASSWORD exits nonzero"
if docker run --rm --platform "${PLATFORM}" --user "${RUN_AS}" -e PASEO_PASSWORD= \
    --tmpfs /home/paseo:rw,uid=1000,gid=1000,mode=0755 \
    --tmpfs /workspace:rw,uid=1000,gid=1000,mode=0755 \
    "${IMAGE}" >"${SMOKE_ROOT}/emptypw.out" 2>"${SMOKE_ROOT}/emptypw.err"; then
    die "container started with an empty PASEO_PASSWORD"
fi
ok "empty PASEO_PASSWORD exits nonzero"

start_daemon() {
    local name="$1"
    shift
    docker run -d --name "${name}" --platform "${PLATFORM}" "$@" \
        -e PASEO_PASSWORD="${PW}" \
        -e PASEO_RELAY_ENABLED=false \
        -e PASEO_HOSTNAMES=paseo.smoke.invalid \
        -e PASEO_LISTEN=127.0.0.1:6767 \
        --tmpfs /home/paseo:rw,uid=1000,gid=1000,mode=0755 \
        --tmpfs /workspace:rw,uid=1000,gid=1000,mode=0755 \
        "${IMAGE}" >/dev/null
}

wait_health() {
    local name="$1" code=""
    for _ in $(seq 1 90); do
        if [[ "$(docker inspect -f '{{.State.Running}}' "${name}" 2>/dev/null || true)" != "true" ]]; then
            docker logs "${name}" >&2 || true
            return 1
        fi
        code="$(docker exec "${name}" curl -s -o /dev/null -w '%{http_code}' \
            http://127.0.0.1:6767/api/health 2>/dev/null || true)"
        [[ "${code}" == "200" ]] && return 0
        sleep 1
    done
    docker logs "${name}" >&2 || true
    return 1
}

# Root start: proves the upstream entrypoint drops the daemon to UID 1000.
start_daemon "${CONTAINER_ROOT}"
wait_health "${CONTAINER_ROOT}" || die "daemon did not become healthy after a root start"
ok "health endpoint returns 200 after a root start"
daemon_uids="$(docker exec "${CONTAINER_ROOT}" sh -c '
  for p in /proc/[0-9]*; do
    IFS= read -r comm <"$p/comm" 2>/dev/null || continue
    [ "$comm" = node ] || continue
    awk "/^Uid:/{print \$2}" "$p/status"
  done
')"
assert_eq "daemon Node process runs as UID 1000" "${daemon_uids}" "1000"
assert_eq "disallowed Host is rejected" \
    "$(docker exec "${CONTAINER_ROOT}" curl -s -o /dev/null -w '%{http_code}' \
        -H 'Host: evil.invalid' http://127.0.0.1:6767/api/health)" "403"
if docker exec -u 1000 -e HOME=/tmp/smoke-noauth -e PASEO_HOME=/tmp/smoke-noauth/.paseo -e PASEO_PASSWORD= "${CONTAINER_ROOT}" \
    paseo ls --host 127.0.0.1:6767 >/dev/null 2>&1; then
    die "unauthenticated control request succeeded"
fi
ok "control rejects missing credentials"
if docker exec -u 1000 -e HOME=/tmp/smoke-wrong -e PASEO_HOME=/tmp/smoke-wrong/.paseo -e PASEO_PASSWORD=wrong-password "${CONTAINER_ROOT}" \
    paseo ls --host 127.0.0.1:6767 >/dev/null 2>&1; then
    die "control request with the wrong password succeeded"
fi
ok "control rejects wrong credentials"
docker exec -u 1000 -e HOME=/tmp/smoke-correct -e PASEO_HOME=/tmp/smoke-correct/.paseo -e PASEO_PASSWORD="${PW}" "${CONTAINER_ROOT}" \
    paseo ls --host 127.0.0.1:6767 >/dev/null ||
    die "authenticated control request failed"
ok "authenticated CLI control succeeds"
docker rm -f "${CONTAINER_ROOT}" >/dev/null

# Deployment posture: start as 1000:1000 with independent fresh mounts.
start_daemon "${CONTAINER}" --user "${RUN_AS}"
wait_health "${CONTAINER}" || die "daemon did not become healthy as 1000:1000"
ok "health endpoint returns 200 as UID 1000"
root_uids="$(docker exec "${CONTAINER}" sh -c '
  for p in /proc/[0-9]*; do
    [ -r "$p/status" ] || continue
    awk "/^Uid:/{if (\$2 == 0) print \$2}" "$p/status"
  done
')"
if [[ -n "${root_uids}" ]]; then
    die "container started as 1000:1000 still runs a root process"
fi
ok "container started as 1000:1000 runs no root process"
nonroot_uids="$(docker exec "${CONTAINER}" sh -c '
  for p in /proc/[0-9]*; do
    IFS= read -r comm <"$p/comm" 2>/dev/null || continue
    [ "$comm" = node ] || continue
    awk "/^Uid:/{print \$2}" "$p/status"
  done
')"
assert_eq "daemon Node process runs as UID 1000 when started non-root" "${nonroot_uids}" "1000"
if docker exec -u 1000 -e HOME=/tmp/smoke-noauth -e PASEO_HOME=/tmp/smoke-noauth/.paseo -e PASEO_PASSWORD= "${CONTAINER}" \
    paseo ls --host 127.0.0.1:6767 >/dev/null 2>&1; then
    die "non-root daemon accepted missing control credentials"
fi
ok "non-root daemon rejects missing credentials"
if docker exec -u 1000 -e HOME=/tmp/smoke-wrong -e PASEO_HOME=/tmp/smoke-wrong/.paseo -e PASEO_PASSWORD=wrong-password "${CONTAINER}" \
    paseo ls --host 127.0.0.1:6767 >/dev/null 2>&1; then
    die "non-root daemon accepted wrong control credentials"
fi
ok "non-root daemon rejects wrong credentials"
docker exec -u 1000 -e HOME=/tmp/smoke-correct -e PASEO_HOME=/tmp/smoke-correct/.paseo -e PASEO_PASSWORD="${PW}" "${CONTAINER}" \
    paseo ls --host 127.0.0.1:6767 >/dev/null ||
    die "authenticated control failed as UID 1000"
ok "authenticated CLI control succeeds as UID 1000"
docker rm -f "${CONTAINER}" >/dev/null

echo "== all ${PASS} assertions passed =="
