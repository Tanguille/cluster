#!/usr/bin/env bash
# Build a Talos installer carrying a custom Linux kernel, without forking siderolabs.
#
# Two version inputs, nothing else to keep in sync: the kernel version (Renovate-managed,
# in docker/talos-kernel/Dockerfile) and the Talos version (from the tuppr CR). The toolchain
# and kernel-config pins are read out of the Talos release's own Makefile, so they cannot
# drift from the release being built. See docker/talos-kernel/README.md.
set -euo pipefail

KERNEL_VERSION="${1:?usage: talos-kernel-build.sh <kernel-version> <talos-version> [node...]}"
TALOS_VERSION="${2:?usage: talos-kernel-build.sh <kernel-version> <talos-version> [node...]}"
shift 2
NODES=("$@")

REGISTRY="${REGISTRY:-ghcr.io}"
USERNAME="${USERNAME:-tanguille}"
PREFIX="${REGISTRY}/${USERNAME}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# tuppr compares this to the version the node reports, so the kernel has to be IN the string
# or a kernel-only bump is invisible to it. See README.md "Version tagging".
VERSION="${TALOS_VERSION}-k${KERNEL_VERSION}"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

log "talos ${TALOS_VERSION} + linux ${KERNEL_VERSION} -> ${VERSION}"
git clone -q --depth 1 --branch "${TALOS_VERSION}" \
    https://github.com/siderolabs/talos.git "${WORK}/talos"
TOOLS_REV="$(sed -nE 's/^TOOLS \?= (.*)$/\1/p' "${WORK}/talos/Makefile")"
PKGS_REV="$(sed -nE 's/^PKGS \?= (.*)$/\1/p' "${WORK}/talos/Makefile")"
PKGS_SHA="${PKGS_REV##*-g}"
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
# would regress the GPU microcode.
log "linux-firmware (mirrored, not rebuilt)"
docker pull -q "ghcr.io/siderolabs/linux-firmware:${PKGS_REV}"
docker tag "ghcr.io/siderolabs/linux-firmware:${PKGS_REV}" "${PREFIX}/linux-firmware:${VERSION}"
docker push -q "${PREFIX}/linux-firmware:${VERSION}"

# Must be rebuilt against THIS kernel build: the modules come out of the kernel package, the
# kernel generates a fresh signing key per build, and module.sig_enforce=1 rejects any other.
log "amdgpu extension"
git clone -q --depth 1 --branch "${TALOS_VERSION}" \
    https://github.com/siderolabs/extensions.git "${WORK}/extensions"
make -C "${WORK}/extensions" amdgpu PUSH=true PLATFORM=linux/amd64 \
    REGISTRY="${REGISTRY}" USERNAME="${USERNAME}" \
    PKGS_PREFIX="${PREFIX}" PKGS="${VERSION}"

# talos Dockerfile installs every module in hack/modules-amd64.txt by exact path and fails the
# build on the first one missing, so a kernel bump that moves or drops one breaks the installer
# with an opaque error. Rewrite moved paths, drop vanished modules. The printed list is the
# per-bump review item.
log "reconciling module allowlist"
cid="$(docker create "${PREFIX}/kernel:${VERSION}" /bin/true)"
docker export "${cid}" | tar -C "${WORK}" -xf - 'usr/lib/modules' 2>/dev/null || true
docker rm -f "${cid}" >/dev/null
MODROOT="${WORK}/usr/lib/modules/$(ls "${WORK}/usr/lib/modules")"
LIST="${WORK}/talos/hack/modules-amd64.txt"
while IFS= read -r entry; do
    [[ -n "${entry}" ]] || continue
    if [[ -e "${MODROOT}/${entry}" ]]; then
        printf '%s\n' "${entry}"
        continue
    fi
    hits="$(cd "${MODROOT}" && find . -name "$(basename "${entry}")" -type f | sed 's|^\./||')"
    if [[ "$(grep -c . <<<"${hits}")" == 1 && -n "${hits}" ]]; then
        echo "    MOVED   ${entry} -> ${hits}" >&2
        printf '%s\n' "${hits}"
    else
        echo "    DROPPED ${entry}" >&2
    fi
done < "${LIST}" > "${LIST}.new"
mv "${LIST}.new" "${LIST}"
git -C "${WORK}/talos" -c user.email=noreply@local -c user.name=build \
    commit -qam "reconcile module list for ${KERNEL_VERSION}"

log "installer-base + imager"
for target in installer-base imager; do
    make -C "${WORK}/talos" "${target}" PUSH=true PLATFORM=linux/amd64 \
        INSTALLER_ARCH=targetarch TAG="${VERSION}" \
        REGISTRY="${REGISTRY}" USERNAME="${USERNAME}" \
        PKG_KERNEL="${PREFIX}/kernel:${VERSION}"
done

# One installer per distinct schematic. Flags are derived from the schematic file so the node
# config stays the single source of truth for kernel args and extensions.
DIGESTS="$(crane export "ghcr.io/siderolabs/extensions:${TALOS_VERSION}" - | tar -xO image-digests)"
for node in "${NODES[@]}"; do
    log "installer for ${node}"
    schematic="$(just talos schematic-file "${node}")"
    args=()
    while read -r arg; do args+=(--extra-kernel-arg "${arg}"); done \
        < <(yq -r '.customization.extraKernelArgs[]' "${schematic}")
    while read -r ext; do
        if [[ "${ext}" == "siderolabs/amdgpu" ]]; then
            args+=(--system-extension-image "${PREFIX}/amdgpu:${VERSION}")
        else
            args+=(--system-extension-image "$(grep -F "ghcr.io/${ext}:" <<<"${DIGESTS}" | head -1)")
        fi
    done < <(yq -r '.customization.systemExtensions.officialExtensions[]' "${schematic}")
    docker run --rm -v "${WORK}/out:/out" "${PREFIX}/imager:${VERSION}" installer \
        --arch amd64 --base-installer-image "${PREFIX}/installer-base:${VERSION}" "${args[@]}"
    crane push "${WORK}/out/installer-amd64.tar" "${PREFIX}/installer:${VERSION}-${node}"
done

log "done: ${PREFIX}/installer:${VERSION}-<node>"
