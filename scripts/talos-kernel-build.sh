#!/usr/bin/env bash
# Build a Talos installer carrying a custom Linux kernel, without forking siderolabs.
#
# Nothing has to be typed to run this: the kernel version comes from the Renovate-managed
# ARG in docker/talos-kernel/Dockerfile, the Talos version from the tuppr CR, and the
# toolchain and kernel-config pins out of the Talos release's own Makefile, so none of them
# can drift from the release being built. See docker/talos-kernel/README.md.
set -euo pipefail

TALOS_VERSION="${1:?usage: talos-kernel-build.sh <talos-version> <node>...}"
shift

REGISTRY="${REGISTRY:-ghcr.io}"
USERNAME="${USERNAME:-tanguille}"
PREFIX="${REGISTRY}/${USERNAME}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# Read the kernel version out of the Renovate-managed line rather than taking it as an
# argument: a bump PR is then what changes the build. Taken separately, Renovate could edit
# the Dockerfile while the tag and the built kernel came from whatever number was typed.
KERNEL_VERSION="$(just kernel-version)"

# tuppr compares this to the version the node reports, so the kernel has to be IN the string
# or a kernel-only bump is invisible to it. See README.md "Version tagging".
VERSION="${TALOS_VERSION}-k${KERNEL_VERSION}"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

log "talos ${TALOS_VERSION} + linux ${KERNEL_VERSION} -> ${VERSION}"
git clone -q --depth 1 --branch "${TALOS_VERSION}" \
    https://github.com/siderolabs/talos.git "${WORK}/talos"
TOOLS_REV="$(sed -nE 's/^TOOLS \?= (.*)$/\1/p' "${WORK}/talos/Makefile")"
PKGS_REV="$(sed -nE 's/^PKGS \?= (.*)$/\1/p' "${WORK}/talos/Makefile")"
# PKGS is pinned as vX.Y.Z-N-g<sha>; the strip yields the sha. Guarded because a format change
# upstream would otherwise leave PKGS_SHA holding the whole pin, and the config fetch would 404
# into a build against the wrong kernel config rather than failing here.
PKGS_SHA="${PKGS_REV##*-g}"
[[ "${PKGS_SHA}" =~ ^[0-9a-f]{7,40}$ ]] || {
    echo "PKGS pin '${PKGS_REV}' did not yield a sha (got '${PKGS_SHA}')" >&2; exit 1
}
echo "    derived TOOLS=${TOOLS_REV} PKGS=${PKGS_REV}"

log "kernel package"
docker build \
    --build-arg "KERNEL_VERSION=${KERNEL_VERSION}" \
    --build-arg "TOOLS_REV=${TOOLS_REV}" \
    --build-arg "PKGS_SHA=${PKGS_SHA}" \
    -t "${PREFIX}/kernel:${VERSION}" "${REPO_ROOT}/docker/talos-kernel"
docker push -q "${PREFIX}/kernel:${VERSION}"

# The amdgpu extension wants kernel and linux-firmware at the SAME prefix and tag. Firmware is
# kernel-independent, so mirror upstream's current blobs; rebuilding from an older pkgs tag
# would regress the GPU microcode. Registry-to-registry, so the ~950 MiB of blobs are
# cross-repo mounted rather than pulled through the local daemon and pushed back.
log "linux-firmware (mirrored, not rebuilt)"
crane copy "ghcr.io/siderolabs/linux-firmware:${PKGS_REV}" "${PREFIX}/linux-firmware:${VERSION}"

# Must be rebuilt against THIS kernel build: the modules come out of the kernel package, the
# kernel generates a fresh signing key per build, and module.sig_enforce=1 rejects any other.
log "amdgpu extension"
git clone -q --depth 1 --branch "${TALOS_VERSION}" \
    https://github.com/siderolabs/extensions.git "${WORK}/extensions"
make -C "${WORK}/extensions" amdgpu PUSH=true PLATFORM=linux/amd64 \
    REGISTRY="${REGISTRY}" USERNAME="${USERNAME}" \
    PKGS_PREFIX="${PREFIX}" PKGS="${VERSION}"

# The extension composes its own tag as <firmware-version>-<extensions-tag> (e.g.
# 20260810-v1.14.0-rc.1), NOT ${VERSION}. Discover what was actually published instead of
# assuming: guessing cost a whole pipeline run, with the imager failing on a tag that never
# existed. Filtering on the extensions tag keeps it unambiguous across repeated builds.
EXT_TAG="$(git -C "${WORK}/extensions" describe --tag --always --match 'v[0-9]*')"
AMDGPU_TAG="$(crane ls "${PREFIX}/amdgpu" | grep -F -- "-${EXT_TAG}" | tail -1)"
: "${AMDGPU_TAG:?amdgpu extension was not published under any tag ending in -${EXT_TAG}}"
AMDGPU_REF="${PREFIX}/amdgpu:${AMDGPU_TAG}"
echo "    extension published as ${AMDGPU_REF}"

# talos Dockerfile installs every module in hack/modules-amd64.txt by exact path and fails the
# build on the first one missing, so a kernel bump that moves or drops one breaks the installer
# with an opaque error. Rewrite moved paths, drop vanished modules. The printed list is the
# per-bump review item. Only the member NAMES are needed, so list the tar rather than
# extracting ~250 MiB of modules into tmpfs to run existence tests against.
log "reconciling module allowlist"
KIMG="${PREFIX}/kernel:${VERSION}"
# Exported off the local daemon, not the registry: `docker build` above put this image there,
# so crane would re-download what was just pushed. Streamed rather than copied out, so nothing
# is written to disk. Two passes are cheap now that both read the local layer store; modules.dep
# alone will not do — the list also carries non-.ko entries (modules.builtin, modules.order)
# that exist only in the file listing.
# The image is FROM scratch with no CMD, so `create` needs an argv it never runs -- the
# container is only ever a handle to export, never started.
CID="$(docker create "${KIMG}" /nonexistent)"
: "${CID:?docker create returned no container id for ${KIMG}}"
trap 'docker rm -f "${CID}" >/dev/null 2>&1 || true; rm -rf "${WORK}"' EXIT
MODS="$(docker export "${CID}" | tar -tf - | sed -n 's|^usr/lib/modules/[^/]*/||p')"
DEPS="$(docker export "${CID}" | tar -xO --wildcards 'usr/lib/modules/*/modules.dep')"
LIST="${WORK}/talos/hack/modules-amd64.txt"

# Pass 1: rewrite paths that moved, drop modules the config no longer produces.
while IFS= read -r entry; do
    [[ -n "${entry}" ]] || continue
    if grep -qxF "${entry}" <<<"${MODS}"; then
        printf '%s\n' "${entry}"
    elif hits="$(grep -F "/${entry##*/}" <<<"${MODS}")" && [[ "${hits}" != *$'\n'* ]]; then
        echo "    MOVED   ${entry} -> ${hits}" >&2
        printf '%s\n' "${hits}"
    else
        echo "    DROPPED ${entry}" >&2
    fi
done < "${LIST}" > "${LIST}.stage1"

# Pass 2: close the set over modules.dep. Talos 1.14 fails the installer build when
# `depmod --errsyms` prints anything at all, and 7.x split stmmac_libpci.ko out of
# stmmac-pci.ko, leaving a list written against 6.18 with a dangling dependency. Closing over
# the dependency graph handles that whole class instead of hand-patching each upstream split.
# (1.13 shipped the same dangling dep; it simply had no gate to catch it.)
declare -A DEPOF=() WANT=()
while IFS= read -r line; do
    [[ "${line}" == *:* ]] || continue
    DEPOF["${line%%:*}"]="${line#*:}"
done <<<"${DEPS}"
queue=()
while IFS= read -r e; do [[ -n "${e}" ]] && queue+=("${e}"); done < "${LIST}.stage1"
while ((${#queue[@]})); do
    e="${queue[-1]}"; unset 'queue[-1]'
    [[ -n "${WANT[${e}]:-}" ]] && continue
    WANT["${e}"]=1
    for d in ${DEPOF[${e}]:-}; do
        [[ -n "${WANT[${d}]:-}" ]] || queue+=("${d}")
    done
done
cp "${LIST}.stage1" "${LIST}"
for m in "${!WANT[@]}"; do
    grep -qxF "${m}" "${LIST}.stage1" && continue
    echo "    ADDED   ${m} (dependency)" >&2
    printf '%s\n' "${m}" >> "${LIST}"
done
rm -f "${LIST}.stage1"

# Committed so talos' `SHA ?= $(git describe --dirty)` does not stamp "-dirty" into gendata.
# Only when something changed: `git commit` exits 1 with nothing staged, and under `set -e`
# that kills the build on the most ordinary case there is — a patch bump inside a series that
# needs no reconciliation at all.
if ! git -C "${WORK}/talos" diff --quiet -- hack/modules-amd64.txt; then
    git -C "${WORK}/talos" -c user.email=noreply@local -c user.name=build \
        commit -qam "reconcile module list for ${KERNEL_VERSION}"
else
    echo "    module list unchanged, nothing to commit"
fi

log "installer-base + imager"
make -C "${WORK}/talos" installer-base imager PUSH=true PLATFORM=linux/amd64 \
    INSTALLER_ARCH=targetarch TAG="${VERSION}" \
    REGISTRY="${REGISTRY}" USERNAME="${USERNAME}" \
    PKG_KERNEL="${PREFIX}/kernel:${VERSION}"

# One installer per node, published to a per-node repo.
#
# The node has to be in the REPO PATH, not the tag: tuppr rebuilds the target ref as
# "<repo>:<targetVersion>" (upgrade.go buildTalosUpgradeImage), discarding the current tag
# entirely, so a per-node tag suffix names an image tuppr can never ask for.
#
# A per-node name rather than the schematic id, even though the id is content-addressed and
# would let nodes sharing a schematic share a repo: the id MOVES whenever a schematic is
# edited, and each new id is a new ghcr package that starts private, so every schematic edit
# would silently strand nodes on an unpullable ref. A node name never moves.
DIGESTS="$(crane export "ghcr.io/siderolabs/extensions:${TALOS_VERSION}" - | tar -xO image-digests)"
# Pre-created so it is owned by us: docker would create the bind-mount target as root, and the
# EXIT trap then cannot unlink the imager's output, leaking it and failing the script's exit.
mkdir -p "${WORK}/out"
declare -A BUILT=()
PUBLISHED=()
for node in "$@"; do
    schematic="$(just talos schematic-file "${node}")"
    # One image per SCHEMATIC, not per node — two schematics, two images, however many nodes.
    # The repo is named after the schematic so both are the same kind of thing: `shared` for
    # talos/schematic.yaml, and the node name for a node carrying its own override.
    if [[ "${schematic}" == "${REPO_ROOT}/talos/schematic.yaml" ]]; then
        dst="${PREFIX}/installer/shared:${VERSION}"
    else
        dst="${PREFIX}/installer/${node}:${VERSION}"
    fi
    # Nodes sharing a schematic build byte-identical installers, so the second is a registry
    # copy rather than another imager run.
    if [[ -n "${BUILT[${schematic}]:-}" ]]; then
        # Same schematic means a byte-identical installer. Nodes that also resolve to the same
        # repo (both on the shared schematic) are already done; only a divergent repo needs a
        # copy, and copying a ref onto itself would be a confusing no-op.
        if [[ "${BUILT[${schematic}]}" == "${dst}" ]]; then
            log "installer for ${node}: already published as ${dst}"
        else
            log "installer for ${node}: copying from ${BUILT[${schematic}]}"
            crane copy "${BUILT[${schematic}]}" "${dst}"
            PUBLISHED+=("${dst}")
        fi
        continue
    fi
    log "installer for ${node}"
    # Read into variables first, NOT `< <(yq ...)`. A process substitution's exit status never
    # reaches the enclosing command, so `set -e` cannot see it: a failed yq yields an empty
    # array and the build happily publishes an installer with no kernel args (no
    # module.sig_enforce=1, no talos.platform=metal) or no extensions (no amdgpu). It succeeds
    # and produces a plausible, broken artifact. As assignments the failure aborts here.
    kargs="$(yq -r '.customization.extraKernelArgs[] | "--extra-kernel-arg=" + .' "${schematic}")"
    exts="$(yq -r '.customization.systemExtensions.officialExtensions[]' "${schematic}")"
    [[ -n "${kargs}" && -n "${exts}" ]] || {
        echo "empty kernel args or extensions from ${schematic}" >&2; exit 1
    }
    mapfile -t args <<<"${kargs}"
    while read -r ext; do
        if [[ "${ext}" == "siderolabs/amdgpu" ]]; then
            args+=(--system-extension-image "${AMDGPU_REF}")
        else
            # Also an assignment: in argument position a failed grep would expand to "" and
            # hand the imager --system-extension-image with an empty value.
            digest="$(grep -m1 -F "ghcr.io/${ext}:" <<<"${DIGESTS}")"
            args+=(--system-extension-image "${digest}")
        fi
    done <<<"${exts}"
    docker run --rm -v "${WORK}/out:/out" \
        -v "${HOME}/.docker:/dockercfg:ro" -e DOCKER_CONFIG=/dockercfg \
        "${PREFIX}/imager:${VERSION}" installer \
        --arch amd64 --base-installer-image "${PREFIX}/installer-base:${VERSION}" "${args[@]}"
    crane push "${WORK}/out/installer-amd64.tar" "${dst}"
    BUILT[${schematic}]="${dst}"
    PUBLISHED+=("${dst}")
done

# Reported from here rather than rebuilt in CI: the mapping from schematic to repo lives in the
# loop above, and a second copy in a workflow step drifts. Under Actions this becomes the job
# summary; locally it is the closing log line.
{
    echo "### Talos custom kernel"
    echo "Linux \`${KERNEL_VERSION}\` on Talos \`${TALOS_VERSION}\`"
    echo
    for ref in "${PUBLISHED[@]}"; do echo "- \`${ref}\`"; done
} >> "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
