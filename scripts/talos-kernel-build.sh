#!/usr/bin/env bash
# Build a Talos installer carrying a custom Linux kernel, without forking siderolabs.
#
# Nothing has to be typed to run this: the kernel version comes from the Renovate-managed
# ARG in docker/talos-kernel/Dockerfile, the Talos version from the tuppr CR, and the
# toolchain and kernel-config pins out of the Talos release's own Makefile, so none of them
# can drift from the release being built. See docker/talos-kernel/README.md.
set -euo pipefail

# The full v<talos>-k<kernel> from the tuppr CR: what tuppr compares, so what the tag must be.
# Upstream release tags want the Talos half alone.
VERSION="${1:?usage: talos-kernel-build.sh <version> <node>...}"
shift
TALOS_VERSION="${VERSION%-k*}"

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

# Same Renovate branch bumps both, so a mismatch means a half-applied tree. An installer whose
# tag advertises a kernel it does not carry is caught nowhere else.
[[ "${VERSION#*-k}" == "${KERNEL_VERSION}" ]] || {
    echo "CR names k${VERSION#*-k}, Dockerfile builds ${KERNEL_VERSION}" >&2
    exit 1
}

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# One image per SCHEMATIC, not per node: `shared` for talos/schematic.yaml, else the node name.
# Shared by the pre-check and the publish loop -- if they disagreed, the build would republish a
# tag it had just decided existed.
# Globals: PREFIX, REPO_ROOT, VERSION
# Arguments: $1 is the node name
# Outputs: the installer image reference on stdout
# Returns: 1 when the node has no schematic
installer_ref() {
    local schematic
    schematic="$(just talos schematic-file "$1")"
    [[ -n "${schematic}" ]] || {
        echo "no schematic for $1" >&2
        return 1
    }
    if [[ "${schematic}" == "${REPO_ROOT}/talos/schematic.yaml" ]]; then
        echo "${PREFIX}/installer/shared:${VERSION}"
    else
        echo "${PREFIX}/installer/$1:${VERSION}"
    fi
}

# One node whose installer is re-pushed under its existing tag. Only for a node with its own
# schematic: control-1 gets the NVIDIA modules, and tuppr compares the tag as a plain string, so
# a new tag would be rolled back to the old one. Everything else keeps the never-reuse rule
# below, so `installer/shared` (control-2/3) is never re-pushed with a fresh kernel and key.
FORCE_NODE="${FORCE_NODE:-}"
if [[ -n "${FORCE_NODE}" ]]; then
    [[ " $* " == *" ${FORCE_NODE} "* ]] || {
        echo "FORCE_NODE=${FORCE_NODE} is not one of: $*" >&2
        exit 1
    }
    force_ref="$(installer_ref "${FORCE_NODE}")" || exit 1
    [[ "${force_ref}" != */installer/shared:* ]] || {
        echo "refusing to force ${force_ref}: it is shared by several nodes" >&2
        exit 1
    }
fi

# Tags in installer/* are never reused: one tag is one build, and amdgpu is signed with a key
# the kernel regenerates each time. Without this, any merge touching a trigger path re-pushes
# the same tags with different bytes. Exit 0 -- a no-op rerun is success. PUBLISH holds the only
# refs the publish loop may push: the missing ones plus FORCE_NODE's. An existing ref is skipped
# even when another one is missing, so a partial rebuild cannot swap a published installer's
# kernel and signing key under the nodes running it.
declare -A PUBLISH=()
for node in "$@"; do
    ref="$(installer_ref "${node}")" || exit 1
    if [[ "${node}" == "${FORCE_NODE}" ]]; then
        echo "    forced: ${ref} (old digest $(crane digest "${ref}" 2>/dev/null || echo none))"
        PUBLISH["${ref}"]=1
    elif crane manifest "${ref}" >/dev/null 2>&1; then
        echo "    exists: ${ref}"
    else
        echo "    missing: ${ref}"
        PUBLISH["${ref}"]=1
    fi
done
if ((${#PUBLISH[@]} == 0)); then
    log "every installer for ${VERSION} is already published, nothing to build"
    exit 0
fi

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
    echo "PKGS pin '${PKGS_REV}' did not yield a sha (got '${PKGS_SHA}')" >&2
    exit 1
}
echo "    derived TOOLS=${TOOLS_REV} PKGS=${PKGS_REV}"

# Cloned before the kernel build: the NVIDIA driver pin lives here, and the extensions are built
# from it further down. Version and sha come from the same file the stock extension is built
# from, so the modules cannot drift from the toolkit extension's userspace libraries and GSP
# firmware (which must match the driver exactly). Plain assignments so a failed yq aborts;
# the patterns catch the "null" yq prints for a renamed key.
git clone -q --depth 1 --branch "${TALOS_VERSION}" \
    https://github.com/siderolabs/extensions.git "${WORK}/extensions"
NVIDIA_VARS="${WORK}/extensions/nvidia-gpu/vars.yaml"
NVIDIA_DRIVER_VERSION="$(yq -er '.NVIDIA_DRIVER_PRODUCTION_VERSION' "${NVIDIA_VARS}")"
NVIDIA_DRIVER_SHA256="$(yq -er '.NVIDIA_PKGS_PRODUCTION_AMD64_SHA256' "${NVIDIA_VARS}")"
[[ "${NVIDIA_DRIVER_VERSION}" =~ ^[0-9]+(\.[0-9]+)+$ && "${NVIDIA_DRIVER_SHA256}" =~ ^[0-9a-f]{64}$ ]] || {
    echo "bad NVIDIA pins in ${NVIDIA_VARS}: '${NVIDIA_DRIVER_VERSION}' '${NVIDIA_DRIVER_SHA256}'" >&2
    exit 1
}
echo "    derived NVIDIA driver ${NVIDIA_DRIVER_VERSION}"

log "kernel package"
# The default docker driver can't export a registry cache. CI sets a docker-container builder
# up itself (docker/setup-buildx-action), but `just talos kernel-build` also runs this directly on a
# workstation (README.md), which may still be on the default driver.
docker buildx inspect --bootstrap 2>/dev/null | grep -q '^Driver:[[:space:]]*docker-container' ||
    docker buildx create --driver docker-container --use --bootstrap >/dev/null
# Registry cache, keyed on the Dockerfile + build-args rather than the target tag: a rerun
# against the same kernel/talos version (e.g. retrying after a package-permission fix) hits
# every layer instead of repeating the ~1-2h ThinLTO compile. Same package as the image
# itself so it rides the access grant that package already has, rather than needing its own.
# :buildcache is a BuildKit cache manifest, not a version ref anything else resolves against —
# deliberately the one mutable tag in an otherwise fully pinned pipeline (see README.md
# "Version tagging" for why every other tag here is one build, never reused).
docker buildx build \
    --build-arg "KERNEL_VERSION=${KERNEL_VERSION}" \
    --build-arg "TOOLS_REV=${TOOLS_REV}" \
    --build-arg "PKGS_SHA=${PKGS_SHA}" \
    --build-arg "NVIDIA_DRIVER_VERSION=${NVIDIA_DRIVER_VERSION}" \
    --build-arg "NVIDIA_DRIVER_SHA256=${NVIDIA_DRIVER_SHA256}" \
    --cache-from "type=registry,ref=${PREFIX}/kernel:buildcache" \
    --cache-to "type=registry,ref=${PREFIX}/kernel:buildcache,mode=min" \
    -t "${PREFIX}/kernel:${VERSION}" --push "${REPO_ROOT}/docker/talos-kernel"

# The amdgpu extension wants kernel and linux-firmware at the SAME prefix and tag. Firmware is
# kernel-independent, so mirror upstream's current blobs; rebuilding from an older pkgs tag
# would regress the GPU microcode. Registry-to-registry, so the ~950 MiB of blobs are
# cross-repo mounted rather than pulled through the local daemon and pushed back.
log "linux-firmware (mirrored, not rebuilt)"
crane copy "ghcr.io/siderolabs/linux-firmware:${PKGS_REV}" "${PREFIX}/linux-firmware:${VERSION}"

# Must be rebuilt against THIS kernel build: the modules come out of the kernel package, the
# kernel generates a fresh signing key per build, and module.sig_enforce=1 rejects any other.
log "amdgpu extension"
make -C "${WORK}/extensions" amdgpu PUSH=true PLATFORM=linux/amd64 \
    REGISTRY="${REGISTRY}" USERNAME="${USERNAME}" \
    PKGS_PREFIX="${PREFIX}" PKGS="${VERSION}"

# The extension composes its own tag as <firmware-version>-<extensions-tag> (e.g.
# 20260810-v1.14.0-rc.2), NOT ${VERSION}. Discover what was actually published instead of
# assuming: guessing cost a whole pipeline run, with the imager failing on a tag that never
# existed. Filtering on the extensions tag keeps it unambiguous across repeated builds.
EXT_TAG="$(git -C "${WORK}/extensions" describe --tag --always --match 'v[0-9]*')"
AMDGPU_TAG="$(crane ls "${PREFIX}/amdgpu" | grep -F -- "-${EXT_TAG}" | tail -1)"
: "${AMDGPU_TAG:?amdgpu extension was not published under any tag ending in -${EXT_TAG}}"
AMDGPU_REF="${PREFIX}/amdgpu:${AMDGPU_TAG}"
echo "    extension published as ${AMDGPU_REF}"

# The NVIDIA modules were compiled and signed inside the kernel build above (Dockerfile, builder
# stage). Upstream's nvidia-open-gpu-kernel-modules-production extension only copies
# /usr/lib/modules/* out of ${PKGS_PREFIX}/<name>-pkg:${PKGS}, so give it that image: the
# kernel image's extra/ and nothing else. Derived from the pushed kernel image, never from a
# second build of the builder stage (a cache miss there means a new signing key). Nothing but
# .ko files go in, so the signing key never reaches an image. The <rel> directory fails the
# COPY loudly if the kernel release is not <version>-talos.
log "nvidia modules package + extension"
KIMG="${PREFIX}/kernel:${VERSION}"
mkdir "${WORK}/nvidia-pkg-ctx"
docker buildx build --platform linux/amd64 \
    --build-arg "KIMG=${KIMG}" --build-arg "REL=${KERNEL_VERSION}-talos" \
    -t "${PREFIX}/nvidia-open-gpu-kernel-modules-production-pkg:${VERSION}" --push \
    -f - "${WORK}/nvidia-pkg-ctx" <<'EOF'
ARG KIMG
FROM ${KIMG} AS kernel
FROM scratch
ARG REL
COPY --from=kernel /usr/lib/modules/${REL}/extra/ /usr/lib/modules/${REL}/extra/
EOF
make -C "${WORK}/extensions" nvidia-open-gpu-kernel-modules-production \
    PUSH=true PLATFORM=linux/amd64 \
    REGISTRY="${REGISTRY}" USERNAME="${USERNAME}" \
    PKGS_PREFIX="${PREFIX}" PKGS="${VERSION}"

# Tag is <driver>-<extensions-tag>, discovered like amdgpu's rather than assumed.
NVIDIA_TAG="$(crane ls "${PREFIX}/nvidia-open-gpu-kernel-modules-production" |
    grep -F -- "${NVIDIA_DRIVER_VERSION}-${EXT_TAG}" | tail -1 || true)"
: "${NVIDIA_TAG:?nvidia modules extension was not published under ${NVIDIA_DRIVER_VERSION}-${EXT_TAG}}"
NVIDIA_MODULES_REF="${PREFIX}/nvidia-open-gpu-kernel-modules-production:${NVIDIA_TAG}"
echo "    extension published as ${NVIDIA_MODULES_REF}"

# talos Dockerfile installs every module in hack/modules-amd64.txt by exact path and fails the
# build on the first one missing, so a kernel bump that moves or drops one breaks the installer
# with an opaque error. Rewrite moved paths, drop vanished modules. The printed list is the
# per-bump review item. Only the member NAMES are needed, so list the tar rather than
# extracting ~250 MiB of modules into tmpfs to run existence tests against.
log "reconciling module allowlist"
# Two streamed exports rather than one local copy: only names and modules.dep are needed, so
# nothing is written to disk. modules.dep alone will not do — the list also carries non-.ko
# entries (modules.builtin, modules.order) that exist only in the file listing. The kernel
# build above pushes via buildx with no --load, so the local daemon never has this image —
# crane's registry export is the only path, and streams rather than materializing the layers.
MODS="$(crane export "${KIMG}" - | tar -tf - | sed -n 's|^usr/lib/modules/[^/]*/||p')"
DEPS="$(crane export "${KIMG}" - | tar -xO --wildcards 'usr/lib/modules/*/modules.dep')"
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
done <"${LIST}" >"${LIST}.stage1"

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
while IFS= read -r e; do [[ -n "${e}" ]] && queue+=("${e}"); done <"${LIST}.stage1"
while ((${#queue[@]})); do
    e="${queue[-1]}"
    unset 'queue[-1]'
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
    printf '%s\n' "${m}" >>"${LIST}"
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

# One installer per schematic (installer_ref). The schematic sits in the REPO PATH, not the tag:
# tuppr rebuilds "<repo>:<targetVersion>" (upgrade.go buildTalosUpgradeImage) and discards the
# current tag, so a tag suffix names an image tuppr can never ask for. Named, not by schematic
# id: the id moves on every edit and each new id is a new ghcr package that starts private.
DIGESTS="$(crane export "ghcr.io/siderolabs/extensions:${TALOS_VERSION}" - | tar -xO image-digests)"
# Pre-created so it is owned by us: docker would create the bind-mount target as root, and the
# EXIT trap then cannot unlink the imager's output, leaking it and failing the script's exit.
mkdir -p "${WORK}/out"
declare -A BUILT=()
PUBLISHED=()
for node in "$@"; do
    schematic="$(just talos schematic-file "${node}")"
    dst="$(installer_ref "${node}")"
    # See PUBLISH above: an existing ref is never re-pushed, whatever its siblings need.
    if [[ -z "${PUBLISH[${dst}]:-}" ]]; then
        log "skip ${node}: ${dst} already published"
        continue
    fi
    # A schematic path fixes the repo (installer_ref), so nodes sharing one share the published
    # installer and need neither another imager run nor a copy.
    if [[ -n "${BUILT[${schematic}]:-}" ]]; then
        log "installer for ${node}: already published as ${dst}"
        continue
    fi
    log "installer for ${node}"
    # Read into variables first, NOT `< <(yq ...)`. A process substitution's exit status never
    # reaches the enclosing command, so `set -e` cannot see it: a failed yq yields an empty
    # array and the build happily publishes an installer with no kernel args (no
    # mitigations=off, no amdgpu.* args) or no extensions (no amdgpu). It succeeds
    # and produces a plausible, broken artifact. As assignments the failure aborts here.
    kargs="$(yq -r '.customization.extraKernelArgs[] | "--extra-kernel-arg=" + .' "${schematic}")"
    exts="$(yq -r '.customization.systemExtensions.officialExtensions[]' "${schematic}")"
    [[ -n "${kargs}" && -n "${exts}" ]] || {
        echo "empty kernel args or extensions from ${schematic}" >&2
        exit 1
    }
    mapfile -t args <<<"${kargs}"
    while read -r ext; do
        if [[ "${ext}" == "siderolabs/amdgpu" ]]; then
            args+=(--system-extension-image "${AMDGPU_REF}")
        elif [[ "${ext}" == "siderolabs/nvidia-open-gpu-kernel-modules-production" ]]; then
            args+=(--system-extension-image "${NVIDIA_MODULES_REF}")
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
    # Assignment, not inline in the array: a failed substitution there is invisible to set -e.
    pushed="$(crane digest "${dst}")"
    BUILT[${schematic}]="${dst}"
    PUBLISHED+=("${dst}@${pushed}")
done

# Reported from here rather than rebuilt in CI: the mapping from schematic to repo lives in the
# loop above, and a second copy in a workflow step drifts. Under Actions this becomes the job
# summary; locally it is the closing log line.
{
    echo "### Talos custom kernel"
    echo "Linux \`${KERNEL_VERSION}\` on Talos \`${TALOS_VERSION}\`"
    echo
    for ref in "${PUBLISHED[@]}"; do echo "- \`${ref}\`"; done
} >>"${GITHUB_STEP_SUMMARY:-/dev/stdout}"
