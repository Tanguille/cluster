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
KERNEL_DOCKERFILE="${REPO_ROOT}/docker/talos-kernel/Dockerfile"
KERNEL_VERSION="$(sed -nE 's/^ARG KERNEL_VERSION=(.+)$/\1/p' "${KERNEL_DOCKERFILE}")"
: "${KERNEL_VERSION:?no 'ARG KERNEL_VERSION=' line in ${KERNEL_DOCKERFILE}}"

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

# talos Dockerfile installs every module in hack/modules-amd64.txt by exact path and fails the
# build on the first one missing, so a kernel bump that moves or drops one breaks the installer
# with an opaque error. Rewrite moved paths, drop vanished modules. The printed list is the
# per-bump review item. Only the member NAMES are needed, so list the tar rather than
# extracting ~250 MiB of modules into tmpfs to run existence tests against.
log "reconciling module allowlist"
MODS="$(crane export "${PREFIX}/kernel:${VERSION}" - | tar -tf - \
    | sed -n 's|^usr/lib/modules/[^/]*/||p')"
LIST="${WORK}/talos/hack/modules-amd64.txt"
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
done < "${LIST}" > "${LIST}.new"
mv "${LIST}.new" "${LIST}"
# Committed so talos' `SHA ?= $(git describe --dirty)` does not stamp "-dirty" into gendata.
git -C "${WORK}/talos" -c user.email=noreply@local -c user.name=build \
    commit -qam "reconcile module list for ${KERNEL_VERSION}"

log "installer-base + imager"
make -C "${WORK}/talos" installer-base imager PUSH=true PLATFORM=linux/amd64 \
    INSTALLER_ARCH=targetarch TAG="${VERSION}" \
    REGISTRY="${REGISTRY}" USERNAME="${USERNAME}" \
    PKG_KERNEL="${PREFIX}/kernel:${VERSION}"

# One installer per distinct schematic, published under the schematic id.
#
# The id has to be in the REPO PATH, not the tag: tuppr rebuilds the target ref as
# "<repo>:<targetVersion>" (upgrade.go buildTalosUpgradeImage), discarding the current tag
# entirely, so a per-node tag suffix is a tag tuppr can never ask for. Under the id, the ref
# is exactly what tuppr's factory-url mode composes as "<base>/<schematic>:<version>", and
# nodes sharing a schematic (control-2 and control-3) share one artifact for free.
DIGESTS="$(crane export "ghcr.io/siderolabs/extensions:${TALOS_VERSION}" - | tar -xO image-digests)"
# Pre-created so it is owned by us: docker would create the bind-mount target as root, and the
# EXIT trap then cannot unlink the imager's output, leaking it and failing the script's exit.
mkdir -p "${WORK}/out"
declare -A PUSHED=()
for node in "$@"; do
    schematic="$(just talos schematic-file "${node}")"
    id="$(just talos schematic-id "${node}")"
    if [[ -n "${PUSHED[${id}]:-}" ]]; then
        echo "    ${node}: same schematic as ${PUSHED[${id}]}, already published"
        continue
    fi
    log "installer for ${node} (schematic ${id})"
    mapfile -t args < <(yq -r '.customization.extraKernelArgs[] | "--extra-kernel-arg=" + .' "${schematic}")
    while read -r ext; do
        if [[ "${ext}" == "siderolabs/amdgpu" ]]; then
            args+=(--system-extension-image "${PREFIX}/amdgpu:${VERSION}")
        else
            args+=(--system-extension-image "$(grep -m1 -F "ghcr.io/${ext}:" <<<"${DIGESTS}")")
        fi
    done < <(yq -r '.customization.systemExtensions.officialExtensions[]' "${schematic}")
    docker run --rm -v "${WORK}/out:/out" "${PREFIX}/imager:${VERSION}" installer \
        --arch amd64 --base-installer-image "${PREFIX}/installer-base:${VERSION}" "${args[@]}"
    crane push "${WORK}/out/installer-amd64.tar" "${PREFIX}/installer/${id}:${VERSION}"
    PUSHED[${id}]="${node}"
done

log "done: ${PREFIX}/installer/<schematic-id>:${VERSION}"
