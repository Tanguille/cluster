#!/usr/bin/env bash
# Build a Talos installer carrying a custom Linux kernel, without forking siderolabs.
# See docker/talos-kernel/README.md for the why and the per-bump maintenance surface.
set -euo pipefail

KERNEL_VERSION="${1:?usage: talos-kernel-build.sh <kernel-version> <talos-version>}"
TALOS_VERSION="${2:?usage: talos-kernel-build.sh <kernel-version> <talos-version>}"
REGISTRY="${REGISTRY:-ghcr.io}"
USERNAME="${USERNAME:-tanguille}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
PKG_DIR="${REPO_ROOT}/docker/talos-kernel"
WORK="${WORK:-$(mktemp -d)}"
PREFIX="${REGISTRY}/${USERNAME}"
# tuppr compares this string to what the node reports, so it must be the image tag AND
# the TAG baked into the Talos build. See README.md "Version tagging".
VERSION="${TALOS_VERSION}-k${KERNEL_VERSION}"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

log "kernel ${KERNEL_VERSION} -> ${PREFIX}/kernel:${VERSION}"
docker build --build-arg "KERNEL_VERSION=${KERNEL_VERSION}" \
    -t "${PREFIX}/kernel:${VERSION}" "${PKG_DIR}"
docker push "${PREFIX}/kernel:${VERSION}"

# The amdgpu extension wants kernel and linux-firmware at the SAME prefix and tag.
# Firmware is kernel-independent, so mirror upstream's current blobs rather than rebuild
# them — rebuilding from an older pkgs tag would regress the GPU microcode.
log "mirroring linux-firmware"
PKGS_TAG="$(docker run --rm --entrypoint /bin/sh "ghcr.io/siderolabs/installer:${TALOS_VERSION}" \
    -c 'cat /usr/share/talos/pkgs 2>/dev/null' 2>/dev/null || true)"
PKGS_TAG="${PKGS_TAG:-${PKGS_TAG_OVERRIDE:?could not detect PKGS tag; set PKGS_TAG_OVERRIDE}}"
docker pull "ghcr.io/siderolabs/linux-firmware:${PKGS_TAG}"
docker tag "ghcr.io/siderolabs/linux-firmware:${PKGS_TAG}" "${PREFIX}/linux-firmware:${VERSION}"
docker push "${PREFIX}/linux-firmware:${VERSION}"

log "amdgpu extension (must match this exact kernel build: module.sig_enforce=1)"
git clone --depth 1 --branch "${TALOS_VERSION}" \
    https://github.com/siderolabs/extensions.git "${WORK}/extensions"
make -C "${WORK}/extensions" amdgpu PUSH=true PLATFORM=linux/amd64 \
    REGISTRY="${REGISTRY}" USERNAME="${USERNAME}" \
    PKGS_PREFIX="${PREFIX}" PKGS="${VERSION}"

log "talos installer-base + imager"
git clone --depth 1 --branch "${TALOS_VERSION}" \
    https://github.com/siderolabs/talos.git "${WORK}/talos"
# Reconcile the module allowlist against what this kernel actually built; the copy in
# talos/Dockerfile fails the build on any listed module the config did not produce.
"${PKG_DIR}/reconcile-modules.sh" "${PREFIX}/kernel:${VERSION}" \
    "${WORK}/talos/hack/modules-amd64.txt"
git -C "${WORK}/talos" -c user.email=noreply@local -c user.name=build \
    commit -qam "reconcile module list for ${KERNEL_VERSION}"
for target in installer-base imager; do
    make -C "${WORK}/talos" "${target}" PUSH=true PLATFORM=linux/amd64 \
        INSTALLER_ARCH=targetarch TAG="${VERSION}" \
        REGISTRY="${REGISTRY}" USERNAME="${USERNAME}" \
        PKG_KERNEL="${PREFIX}/kernel:${VERSION}"
done

log "done: ${PREFIX}/imager:${VERSION}"
cat <<EOF

Next: render one installer per schematic, then push it.
  just talos kernel-installer <node> ${VERSION}
EOF
