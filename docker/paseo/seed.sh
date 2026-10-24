#!/usr/bin/env bash
# Init-container seeding for the Paseo image.
#
# Creates the writable config directories, installs the default Paseo config
# only when none exists, and clones the two approved public repositories only
# when their destinations are absent. It accepts no repository or user input.
#
# Guarantees:
#   * an existing destination (file, directory or symlink, including a dangling
#     one) is never touched: no pull, reset, clean, overwrite or chown;
#   * a clone is made in a temporary sibling on the same filesystem and renamed
#     into place only after success and a second absence check, so an
#     interrupted clone can never look like a finished checkout;
#   * cleanup removes only the temporary path this invocation created;
#   * private repositories (rsume) are never cloned here.
set -euo pipefail

readonly DEFAULT_CONFIG="/opt/paseo/config.json"
readonly WORKSPACE="/workspace"
readonly CLUSTER_URL="https://github.com/Tanguille/cluster.git"
readonly MESH_ORGANISER_URL="https://github.com/Tanguille/mesh-organiser.git"

HOME_DIR="${HOME:?HOME must be set}"
PASEO_HOME_DIR="${PASEO_HOME:?PASEO_HOME must be set}"
HERMES_HOME_DIR="${HERMES_HOME:-${HOME_DIR}/.hermes}"
CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-${HOME_DIR}/.claude}"
CODEX_HOME="${CODEX_HOME:-${HOME_DIR}/.codex}"
XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME_DIR}/.config}"
XDG_DATA_HOME="${XDG_DATA_HOME:-${HOME_DIR}/.local/share}"
XDG_STATE_HOME="${XDG_STATE_HOME:-${HOME_DIR}/.local/state}"
XDG_CACHE_HOME="${XDG_CACHE_HOME:-${HOME_DIR}/.cache}"

TEMP_PATH=""

cleanup() {
  if [[ -n "${TEMP_PATH}" && -d "${TEMP_PATH}" ]]; then
    rm -rf -- "${TEMP_PATH}"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

log() { printf '[paseo-seed] %s\n' "$*"; }

for dir in "${HOME_DIR}" "${PASEO_HOME_DIR}" "${HERMES_HOME_DIR}" \
  "${CLAUDE_CONFIG_DIR}" "${CODEX_HOME}" "${XDG_CONFIG_HOME}" \
  "${XDG_DATA_HOME}" "${XDG_STATE_HOME}" "${XDG_CACHE_HOME}" "${WORKSPACE}"; do
  mkdir -p -- "${dir}"
done

# Default config: never replace a file, directory or symlink already there.
if [[ ! -e "${PASEO_HOME_DIR}/config.json" && ! -L "${PASEO_HOME_DIR}/config.json" ]]; then
  cp -- "${DEFAULT_CONFIG}" "${PASEO_HOME_DIR}/config.json"
  chmod 0644 "${PASEO_HOME_DIR}/config.json"
  log "installed default config at ${PASEO_HOME_DIR}/config.json"
else
  log "keeping existing ${PASEO_HOME_DIR}/config.json"
fi

clone_if_absent() {
  local url="$1" destination="$2" name
  name="$(basename -- "${destination}")"

  if [[ -e "${destination}" || -L "${destination}" ]]; then
    log "keeping existing ${destination}"
    return 0
  fi

  TEMP_PATH="$(mktemp -d --tmpdir="$(dirname -- "${destination}")" ".${name}.seed.XXXXXX")"
  if ! GIT_TERMINAL_PROMPT=0 git clone --branch main -- "${url}" "${TEMP_PATH}"; then
    log "failed to clone ${url}; nothing was installed"
    exit 1
  fi

  # A concurrent writer may have created the destination while cloning. GNU
  # mv -Tn uses a no-clobber rename and -T forbids treating a new directory as
  # a target directory. If publication loses the race, discard only our clone.
  if [[ -e "${destination}" || -L "${destination}" ]]; then
    log "keeping existing ${destination}"
    cleanup
    TEMP_PATH=""
    return 0
  fi

  mv -Tn -- "${TEMP_PATH}" "${destination}"
  if [[ -e "${TEMP_PATH}" || -L "${TEMP_PATH}" ]]; then
    log "keeping concurrently created ${destination}"
    cleanup
    TEMP_PATH=""
    return 0
  fi
  TEMP_PATH=""
  log "cloned ${url} into ${destination}"
}

clone_if_absent "${CLUSTER_URL}" "${WORKSPACE}/cluster"
clone_if_absent "${MESH_ORGANISER_URL}" "${WORKSPACE}/mesh-organiser"

log "seeding complete"
