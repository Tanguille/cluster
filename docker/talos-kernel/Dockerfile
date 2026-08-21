# Talos-compatible Linux kernel package, built WITHOUT forking siderolabs/pkgs.
#
# talos/Dockerfile consumes ${PKG_KERNEL} as a plain base image and reads exactly
# three paths out of it, so any image providing them works:
#   /boot/vmlinuz                     (talos Dockerfile :666, :1064)
#   /usr/lib/modules/<release>/       (:681)
#   /usr/share/spdx/kernel.spdx.json  (:919 — copied unconditionally; the imager
#                                      build fails with "not found" without it)
#
# Rationale for the toolchain choice and for what is and is not vendored: README.md.

# Declared once, before the first FROM, so both stages inherit the same value via a bare
# `ARG KERNEL_VERSION`. Two declarations with only one default silently produced an SPDX
# document with an empty version and an unmatchable CPE on any build not passing --build-arg.
# renovate: datasource=custom.linux-kernel depName=linux
ARG KERNEL_VERSION=7.1.9

# Passed by scripts/talos-kernel-build.sh, read out of the Talos release's own Makefile. No
# default on purpose: a stale one would silently build against the wrong toolchain.
ARG TOOLS_REV

# Verification needs gpg, which the Talos toolchain images do not carry, so the download
# happens in its own stage and only the extracted tree crosses into the build.
FROM alpine:3.24 AS source
RUN apk add --no-cache gnupg tar xz curl
ARG KERNEL_VERSION

# One committed key covers every version Renovate can bump us to: Greg KH signs mainline and
# stable tarballs alike. The .sign covers the UNCOMPRESSED tar, hence `xz -cd | gpgv`.
# gpgv, not gpg --verify: fixed keyring, no trust model, no auto key fetch.
COPY kernel-signing-key.asc /tmp/
RUN gpg --batch --dearmor -o /tmp/korg.gpg < /tmp/kernel-signing-key.asc \
    && V="${KERNEL_VERSION}" && MAJ="${V%%.*}" \
    && curl -fsSL -O "https://cdn.kernel.org/pub/linux/kernel/v${MAJ}.x/linux-${V}.tar.xz" \
    && curl -fsSL -O "https://cdn.kernel.org/pub/linux/kernel/v${MAJ}.x/linux-${V}.tar.sign" \
    && xz -cd "linux-${V}.tar.xz" | gpgv --keyring /tmp/korg.gpg "linux-${V}.tar.sign" - \
    && mkdir -p /src && tar -xJf "linux-${V}.tar.xz" --strip-components=1 -C /src

# Deliberately after the tarball RUN: a Talos-only bump re-fetches these two small files
# instead of re-downloading 150 MB of kernel source.
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

# olddefconfig resolves the symbol delta by taking upstream defaults for every new option.
# The printed counts are the per-bump review surface: inspect them on a minor bump.
# The `|| true` is scoped to diff alone — diff exits 1 whenever the files differ, which is the
# normal case. Left covering the whole chain it also swallowed a failed olddefconfig and
# compiled against the stale config.
RUN cd /src \
    && cp .config /tmp/config.before \
    && make LLVM=1 olddefconfig \
    && { diff -u /tmp/config.before .config > /tmp/delta.diff || true; } \
    && echo "config delta: +$(grep -c '^+[^+]' /tmp/delta.diff) -$(grep -c '^-[^-]' /tmp/delta.diff)"

# objtool sets up an alternate signal stack sized from the libc SIGSTKSZ constant. musl (this
# builder) fixes that at compile time, while the x86 kernel raises sigaltstack's accepted
# minimum on CPUs with a large XSAVE frame (AMX) -- so on such a host the call fails with
# ENOMEM, which musl renders as the misleading "Out of memory". Nothing is short of memory;
# the stack is just declared too small. Scaling every SIGSTKSZ use keeps the malloc and the
# ss_size consistent. Guarded because a silent no-op here reads as "the fix did not work".
# 16 puts musl's 8 KiB x86_64 SIGSTKSZ at 128 KiB, well past the largest sigframe x86 defines,
# and it costs one short-lived mapping in a build tool -- so err high rather than tune it.
ARG SIGSTKSZ_SCALE=16
RUN cd /src \
    && [[ "${SIGSTKSZ_SCALE}" =~ ^[1-9][0-9]*$ ]] \
    && grep -q '\bSIGSTKSZ\b' tools/objtool/signal.c \
    && sed -i "s/\bSIGSTKSZ\b/(SIGSTKSZ * ${SIGSTKSZ_SCALE})/g" tools/objtool/signal.c

# No separate `make modules`: the top-level Makefile makes modules part of the default goal
# whenever CONFIG_MODULES is set (7.1.9 Makefile:1677 `all: modules`).
RUN cd /src && make LLVM=1 -j"$(nproc)"

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
RUN mkdir -p /rootfs/usr/share/spdx && cat > /rootfs/usr/share/spdx/kernel.spdx.json <<EOF
{
  "spdxVersion": "SPDX-2.3",
  "dataLicense": "CC0-1.0",
  "SPDXID": "SPDXRef-DOCUMENT",
  "name": "kernel",
  "documentNamespace": "https://kernel.org/linux-${KERNEL_VERSION}",
  "creationInfo": { "created": "1970-01-01T00:00:00Z", "creators": ["Tool: bldr"] },
  "packages": [{
    "SPDXID": "SPDXRef-Package-kernel",
    "name": "kernel",
    "versionInfo": "${KERNEL_VERSION}",
    "downloadLocation": "https://cdn.kernel.org/pub/linux/kernel/v${KERNEL_VERSION%%.*}.x/linux-${KERNEL_VERSION}.tar.xz",
    "licenseConcluded": "GPL-2.0-only",
    "licenseDeclared": "GPL-2.0-only",
    "copyrightText": "NOASSERTION",
    "filesAnalyzed": false,
    "externalRefs": [{
      "referenceCategory": "SECURITY",
      "referenceType": "cpe23Type",
      "referenceLocator": "cpe:2.3:o:linux:linux_kernel:${KERNEL_VERSION}:*:*:*:*:*:*:*"
    }]
  }],
  "relationships": [{
    "spdxElementId": "SPDXRef-DOCUMENT",
    "relatedSpdxElement": "SPDXRef-Package-kernel",
    "relationshipType": "DESCRIBES"
  }]
}
EOF
RUN python3 -c "import json;assert json.load(open('/rootfs/usr/share/spdx/kernel.spdx.json'))['packages'][0]['versionInfo']"

FROM scratch
COPY --from=builder /rootfs/ /
