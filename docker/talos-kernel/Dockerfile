# Talos-compatible Linux kernel package, built WITHOUT forking siderolabs/pkgs.
#
# talos/Dockerfile consumes ${PKG_KERNEL} as a plain base image and reads exactly
# three paths out of it, so any image providing them works:
#   /boot/vmlinuz                     (COPY --from=pkg-kernel / pkg-kernel-amd64)
#   /usr/lib/modules/<release>/       (the modules-amd64 stage)
#   /usr/share/spdx/kernel.spdx.json  (sbom bind mount; copied unconditionally, so the
#                                      imager build fails with "not found" without it)
#
# Rationale for the toolchain choice and for what is and is not vendored: README.md.

# Declared once, before the first FROM, so both stages inherit the same value via a bare
# `ARG KERNEL_VERSION`. Two declarations with only one default silently produced an SPDX
# document with an empty version and an unmatchable CPE on any build not passing --build-arg.
# renovate: datasource=custom.linux-kernel depName=linux
ARG KERNEL_VERSION=7.3.4

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

# NVIDIA open modules for the RTX 2070 on control-1. Own stage for the same reason as `source`:
# only the kernel-open tree crosses into the build. Both args come from the Talos release's
# extensions/nvidia-gpu/vars.yaml (scripts/talos-kernel-build.sh), so the driver matches the
# userspace libraries and GSP firmware in the toolkit extension, which hard-requires it. No
# default on purpose, as with TOOLS_REV. --no-same-owner because the archive carries NVIDIA's
# build uid, which a rootless builder cannot chown to.
FROM alpine:3.24 AS nvidia-source
RUN apk add --no-cache curl tar xz
ARG NVIDIA_DRIVER_VERSION
ARG NVIDIA_DRIVER_SHA256
RUN curl -fsSL -o nvidia.tar.xz \
       "https://developer.download.nvidia.com/compute/nvidia-driver/redist/nvidia_driver/linux-x86_64/nvidia_driver-linux-x86_64-${NVIDIA_DRIVER_VERSION}-archive.tar.xz" \
    && echo "${NVIDIA_DRIVER_SHA256}  nvidia.tar.xz" | sha256sum -c - \
    && mkdir -p /nvidia && tar -xJf nvidia.tar.xz --no-same-owner --strip-components=1 -C /nvidia

FROM ghcr.io/siderolabs/llvm:${TOOLS_REV} AS llvm

FROM ghcr.io/siderolabs/tools:${TOOLS_REV} AS builder
SHELL ["/bin/bash", "-c"]
COPY --from=llvm / /
COPY --from=source /src /src
COPY generate-spdx.py /tmp/generate-spdx.py
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
RUN cd /src \
    && grep -q '\bSIGSTKSZ\b' tools/objtool/signal.c \
    && sed -i 's/\bSIGSTKSZ\b/(SIGSTKSZ * 16)/g' tools/objtool/signal.c

# No separate `make modules`: the top-level Makefile makes modules part of the default goal
# whenever CONFIG_MODULES is set (`all: modules`).
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

# SPDX doc per pkgs kernel/kernel/pkg.yaml (see generate-spdx.py); a script, not a heredoc:
# hadolint can't parse an instruction after a heredoc in a multi-stage build.
RUN mkdir -p /rootfs/usr/share/spdx && python3 /tmp/generate-spdx.py "$KERNEL_VERSION"

# NVIDIA modules, built against the tree above and signed by the same per-build key (the
# kernel config has MODULE_SIG_ALL, so NVIDIA's own modules_install signs them). This has to be
# this stage: a second build of it on a cache miss would generate a new key and the modules
# would silently fail to load under module.sig_enforce=1. Flags are pkgs'
# nvidia-open-gpu-kernel-modules pkg.yaml (LLVM binutils for ThinLTO, NVIDIA issue 214).
#
# Installed to a scratch root and only extra/ copied over: modules_install also runs depmod and
# writes modules.* files, which would replace the kernel's own modules.dep. INSTALL_MOD_DIR is
# needed because NVIDIA's Makefile defaults it to kernel/drivers/video. extra/ is outside
# hack/modules-amd64.txt, so installer-base ignores it; it only reaches a node through the
# nvidia-open-gpu-kernel-modules-production extension derived from this image.
COPY --from=nvidia-source /nvidia/kernel-open /nvidia/kernel-open
RUN cd /nvidia/kernel-open \
    && export LLVM=1 ARCH=x86_64 \
    && KERNELRELEASE=$(cat /src/include/config/kernel.release) \
    && make LD=ld.lld OBJDUMP=llvm-objdump -j"$(nproc)" SYSSRC=/src modules \
    && make LD=ld.lld OBJDUMP=llvm-objdump -j"$(nproc)" SYSSRC=/src modules_install \
       INSTALL_MOD_PATH=/nv-rootfs/usr INSTALL_MOD_DIR=extra INSTALL_MOD_STRIP=1 \
    && ! { find /nv-rootfs -name '*.ko' -exec grep -FL '~Module signature appended~' {} + | grep -q .; } \
    && cp -r "/nv-rootfs/usr/lib/modules/${KERNELRELEASE}/extra" "/rootfs/usr/lib/modules/${KERNELRELEASE}/" \
    && ls "/rootfs/usr/lib/modules/${KERNELRELEASE}/extra"

FROM scratch
COPY --from=builder /rootfs/ /
