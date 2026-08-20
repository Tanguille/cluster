# Talos-compatible Linux kernel package, built WITHOUT forking siderolabs/pkgs.
#
# talos/Dockerfile consumes ${PKG_KERNEL} as a plain base image and reads exactly
# three paths out of it, so any image providing them works:
#   /boot/vmlinuz                     (talos Dockerfile :666, :1064)
#   /usr/lib/modules/<release>/       (:681)
#   /usr/share/spdx/kernel.spdx.json  (:919 — copied unconditionally; the imager
#                                      build fails with "not found" without it)
#
# Mirrors siderolabs/pkgs kernel/{prepare,build,kernel}/pkg.yaml. See README.md for
# the per-bump maintenance surface and the full build pipeline.
# syntax=docker/dockerfile:1

FROM fedora:44 AS builder
SHELL ["/bin/bash", "-c"]

# clang is not optional: the Talos config sets CONFIG_LTO_CLANG_THIN=y, so gcc cannot
# build this tree. dwarves supplies pahole for CONFIG_DEBUG_INFO_BTF=y.
RUN dnf -y install --setopt=install_weak_deps=False \
      clang lld llvm dwarves bc bison flex \
      elfutils-libelf-devel openssl openssl-devel \
      python3 cpio kmod rsync zstd perl make patch tar xz gnupg2 \
      findutils diffutils gawk hostname \
    && dnf clean all

# renovate: datasource=custom.linux-kernel depName=linux
ARG KERNEL_VERSION=7.1.9

# One committed key covers every version Renovate can bump us to: Greg KH signs the
# mainline and stable tarballs alike (Linus signs git tags, not tarballs). Verifying the
# tarball signature directly is both simpler and stronger than a bot-maintained sha256 —
# and unlike a checksum, it survives a version bump with no second field to update.
# The .sign covers the UNCOMPRESSED tar, hence the xz -cd pipe.
# gpgv, not gpg --verify: fixed keyring, no trust model, no auto key fetch.
COPY kernel-signing-key.asc /tmp/
RUN gpg --batch --dearmor -o /tmp/korg.gpg < /tmp/kernel-signing-key.asc \
    && V="${KERNEL_VERSION}" MAJ="${KERNEL_VERSION%%.*}" \
    && curl -fsSL -O "https://cdn.kernel.org/pub/linux/kernel/v${MAJ}.x/linux-${V}.tar.xz" \
    && curl -fsSL -O "https://cdn.kernel.org/pub/linux/kernel/v${MAJ}.x/linux-${V}.tar.sign" \
    && xz -cd "linux-${V}.tar.xz" | gpgv --keyring /tmp/korg.gpg "linux-${V}.tar.sign" - \
    && mkdir -p /src && tar -xJf "linux-${V}.tar.xz" --strip-components=1 -C /src \
    && rm -f "linux-${V}.tar.xz" "linux-${V}.tar.sign" /tmp/korg.gpg /tmp/kernel-signing-key.asc \
    && cd /src && make mrproper

COPY config-amd64 /pkg/config-amd64
COPY certs/ /pkg/certs/
COPY patches/ /pkg/patches/

# Upstream aborts on the first patch that will not apply. We dry-run all of them and
# report the full verdict in one pass, so a version bump yields the complete triage list
# instead of surfacing failures one rebuild at a time. Patches that no longer apply are
# skipped, not silently dropped — the build log names them and README.md explains the call.
RUN cd /src \
    && for patch in $(find /pkg/patches -type f -name '*.patch' | sort); do \
         if patch -p1 --dry-run --silent < "$patch" >/dev/null 2>&1; then \
           patch -p1 < "$patch" >/dev/null && echo "APPLIED  $(basename "$patch")"; \
         else \
           echo "SKIPPED  $(basename "$patch") (does not apply to ${KERNEL_VERSION})"; \
         fi; \
       done

# olddefconfig resolves the symbol delta by taking upstream defaults for every new
# option. The printed counts are the per-bump review surface: inspect them on a minor bump.
RUN cd /src \
    && cp /pkg/config-amd64 .config \
    && cp /pkg/certs/* certs/ \
    && cp .config /tmp/config.before \
    && make LLVM=1 olddefconfig \
    && echo "config delta: +$(grep -c '^+[^+]' <(diff -u /tmp/config.before .config) || true)" \
            "-$(grep -c '^-[^-]' <(diff -u /tmp/config.before .config) || true)"

RUN cd /src && make LLVM=1 -j"$(nproc)" && make LLVM=1 -j"$(nproc)" modules

# Layout per pkgs kernel/kernel/pkg.yaml. INSTALL_MOD_STRIP=1 strips before signing, so
# the modules keep the "~Module signature appended~" trailer the amdgpu extension asserts.
RUN cd /src \
    && mkdir -p /rootfs/boot /rootfs/certs \
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
