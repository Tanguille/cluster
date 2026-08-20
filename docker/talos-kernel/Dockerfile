# Talos-compatible Linux kernel package, built WITHOUT forking siderolabs/pkgs.
#
# talos/Dockerfile consumes ${PKG_KERNEL} as a plain base image and reads exactly
# three paths out of it, so any image providing them works:
#   /boot/vmlinuz                     (talos Dockerfile :666, :1064)
#   /usr/lib/modules/<release>/       (:681)
#   /usr/share/spdx/kernel.spdx.json  (:919 — copied unconditionally; the imager
#                                      build fails with "not found" without it)
#
# The toolchain is upstream's own, not a distro's: siderolabs builds this kernel with
# ghcr.io/siderolabs/{tools,llvm} at TOOLS_REV, so using them gives the exact clang the
# config was generated against and pins the compiler to the same ref Talos pins. A distro
# base would drift its clang independently of anything in this repo.
#
# The kernel config and module-signing key are fetched from pkgs at PKGS_SHA rather than
# vendored: they are upstream files we do not modify, and fetching them means they always
# match the Talos version being built instead of needing a manual refresh every minor.
# See README.md for the per-bump maintenance surface.
# syntax=docker/dockerfile:1

# Both pins are passed by scripts/talos-kernel-build.sh, read out of the Talos release's own
# Makefile. No defaults on purpose: a stale default would silently build against the wrong
# toolchain for the Talos version being targeted.
ARG TOOLS_REV

# Verification needs gpg, which the Talos toolchain images do not carry, so the download
# happens in its own stage and only the extracted tree crosses into the build.
FROM alpine:3.22 AS source
RUN apk add --no-cache gnupg tar xz curl

# renovate: datasource=custom.linux-kernel depName=linux
ARG KERNEL_VERSION=7.1.9

# One committed key covers every version Renovate can bump us to: Greg KH signs mainline and
# stable tarballs alike (Linus signs git tags, not tarballs). Verifying the signature directly
# is simpler and stronger than a bot-maintained sha256, and unlike a checksum it survives a
# version bump with no second field to update. The .sign covers the UNCOMPRESSED tar.
# gpgv, not gpg --verify: fixed keyring, no trust model, no auto key fetch.
COPY kernel-signing-key.asc /tmp/
RUN gpg --batch --dearmor -o /tmp/korg.gpg < /tmp/kernel-signing-key.asc \
    && V="${KERNEL_VERSION}" && MAJ="${V%%.*}" \
    && curl -fsSL -O "https://cdn.kernel.org/pub/linux/kernel/v${MAJ}.x/linux-${V}.tar.xz" \
    && curl -fsSL -O "https://cdn.kernel.org/pub/linux/kernel/v${MAJ}.x/linux-${V}.tar.sign" \
    && xz -cd "linux-${V}.tar.xz" | gpgv --keyring /tmp/korg.gpg "linux-${V}.tar.sign" - \
    && mkdir -p /src && tar -xJf "linux-${V}.tar.xz" --strip-components=1 -C /src

# pkgs pins its kernel config alongside the toolchain; keep both on the same Talos release.
ARG PKGS_SHA
RUN cd /src \
    && curl -fsSL -o .config \
       "https://raw.githubusercontent.com/siderolabs/pkgs/${PKGS_SHA}/kernel/build/config-amd64" \
    && curl -fsSL -o certs/x509.genkey \
       "https://raw.githubusercontent.com/siderolabs/pkgs/${PKGS_SHA}/kernel/build/certs/x509.genkey"

FROM ghcr.io/siderolabs/llvm:${TOOLS_REV} AS llvm

FROM ghcr.io/siderolabs/tools:${TOOLS_REV} AS builder
SHELL ["/bin/bash", "-c"]
COPY --from=llvm / /
COPY --from=source /src /src
ARG KERNEL_VERSION

# siderolabs carries 6 patches, all backports for hardware this cluster does not have
# (three are Cadence/Atmel macb ethernet; CONFIG_MACB is not even set) or fixes already
# upstream in 7.x. They are deliberately not vendored: on 7.1.9 the only two that still
# applied were the macb pair, so the whole set contributed nothing to the built kernel while
# costing a triage pass every bump. Re-add individually if a real need appears.

# olddefconfig resolves the symbol delta by taking upstream defaults for every new option.
# The printed counts are the per-bump review surface: inspect them on a minor bump.
RUN cd /src \
    && cp .config /tmp/config.before \
    && make LLVM=1 olddefconfig \
    && diff -u /tmp/config.before .config > /tmp/delta.diff || true \
    && echo "config delta: +$(grep -c '^+[^+]' /tmp/delta.diff || true) -$(grep -c '^-[^-]' /tmp/delta.diff || true)"

RUN cd /src && make LLVM=1 -j"$(nproc)" && make LLVM=1 -j"$(nproc)" modules

# Layout per pkgs kernel/kernel/pkg.yaml. INSTALL_MOD_STRIP=1 strips before signing, so the
# modules keep the "~Module signature appended~" trailer the amdgpu extension asserts.
RUN cd /src \
    && mkdir -p /rootfs/boot \
    && mv arch/x86/boot/bzImage /rootfs/boot/vmlinuz \
    && cp System.map /rootfs/boot/System.map \
    && KERNELRELEASE=$(cat include/config/kernel.release) \
    && make LLVM=1 -j"$(nproc)" modules_install INSTALL_MOD_PATH=/rootfs/usr INSTALL_MOD_STRIP=1 \
    && depmod -b /rootfs/usr -F /rootfs/boot/System.map --errsyms -w "$KERNELRELEASE" \
    && rm -f "/rootfs/usr/lib/modules/$KERNELRELEASE/build" \
    && echo "built $KERNELRELEASE"

# bldr emits this from the sbom: block in pkgs kernel/kernel/pkg.yaml. Fixed timestamp,
# not generated: a moving date would break build reproducibility.
RUN mkdir -p /rootfs/usr/share/spdx \
    && printf '%s' "{\
\"spdxVersion\":\"SPDX-2.3\",\"dataLicense\":\"CC0-1.0\",\"SPDXID\":\"SPDXRef-DOCUMENT\",\
\"name\":\"kernel\",\"documentNamespace\":\"https://kernel.org/linux-${KERNEL_VERSION}\",\
\"creationInfo\":{\"created\":\"1970-01-01T00:00:00Z\",\"creators\":[\"Tool: bldr\"]},\
\"packages\":[{\"SPDXID\":\"SPDXRef-Package-kernel\",\"name\":\"kernel\",\
\"versionInfo\":\"${KERNEL_VERSION}\",\"downloadLocation\":\"https://cdn.kernel.org/pub/linux/kernel/v${KERNEL_VERSION%%.*}.x/linux-${KERNEL_VERSION}.tar.xz\",\
\"licenseConcluded\":\"GPL-2.0-only\",\"licenseDeclared\":\"GPL-2.0-only\",\
\"copyrightText\":\"NOASSERTION\",\"filesAnalyzed\":false,\
\"externalRefs\":[{\"referenceCategory\":\"SECURITY\",\"referenceType\":\"cpe23Type\",\
\"referenceLocator\":\"cpe:2.3:o:linux:linux_kernel:${KERNEL_VERSION}:*:*:*:*:*:*:*\"}]}],\
\"relationships\":[{\"spdxElementId\":\"SPDXRef-DOCUMENT\",\
\"relatedSpdxElement\":\"SPDXRef-Package-kernel\",\"relationshipType\":\"DESCRIBES\"}]}" \
       > /rootfs/usr/share/spdx/kernel.spdx.json \
    && python3 -c "import json;json.load(open('/rootfs/usr/share/spdx/kernel.spdx.json'))"

FROM scratch
COPY --from=builder /rootfs/ /
