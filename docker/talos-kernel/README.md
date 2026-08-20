# Talos kernel package

Builds a Talos-compatible Linux kernel package so the cluster can run a newer kernel
than Talos ships. Talos v1.13.9 and v1.14.0-rc.1 both ship **Linux 6.18.44**; this
tracks kernel.org **stable** (7.1.9 at time of writing).

Neither `siderolabs/talos` nor `siderolabs/pkgs` is forked. Both are consumed at their
release tags and steered with make variables.

## Why

The in-kernel Ceph client gained AES256-KRB5 (`aes256k`) support in **Linux 7.0**
(`b7cc142dbafe libceph: add support for CEPH_CRYPTO_AES256KRB5`, merged in
`ceph-for-7.0-rc1`). It is absent from 6.18.44 and was **not** backported to 6.18.y — it
is a feature, not a fix. Without it the `csi-rbd-node` / `csi-cephfs-node` keys, which
drive `rbd map` and `mount -t ceph`, cannot move off the insecure `aes` key type
deprecated by CVE-2025-30156.

Note the split: the two `HEALTH_ERR` items (`AUTH_INSECURE_SERVICE_KEY_TYPE`,
`AUTH_INSECURE_SERVICE_TICKETS`) are mon/mgr/osd/mds keys — pure userspace, fixed by
`spec.security.cephx.daemon` key rotation with no kernel involvement. Only the client
half needs this.

## Pipeline

Four artifacts, each steered by a make variable. `PKGS`/`PKGS_PREFIX` and `PKG_KERNEL`
are the only handles required — no forks.

```
docker/talos-kernel/Dockerfile   ->  ghcr.io/tanguille/kernel:<tag>
                                       |
siderolabs/pkgs linux-firmware   ------+   (mirrored as-is; firmware is kernel-independent,
  (retagged, not rebuilt)              |    so keep upstream's CURRENT blobs)
                                       v
siderolabs/extensions @ vX.Y.Z    make amdgpu PKGS_PREFIX=... PKGS=<tag>
                                       |
                                       v
siderolabs/talos @ vX.Y.Z         make installer-base imager PKG_KERNEL=... TAG=<version>
                                       |
                                       v
                                  imager installer --system-extension-image ...
                                       ->  ghcr.io/tanguille/installer:<version>
```

`amdgpu` **must** be rebuilt: `drm/amdgpu/pkg.yaml` extracts `amdgpu.ko` and `amdxcp.ko`
out of the kernel package image and asserts every `.ko` carries a signature. The kernel
generates a fresh signing key per build, and the cmdline carries `module.sig_enforce=1`,
so an extension built against a *different build* of the same version will fail to load.
Rebuild the extension from the same kernel image, every time.

`amd-ucode`, `nfsrahead` and `qemu-guest-agent` are firmware/userspace only — reuse the
official images by digest.

## Version tagging

Tag installers `v<talos>-k<kernel>`, e.g. `v1.13.9-k7.1.9`.

`TAG` is embedded into `pkg/machinery/gendata` (talos `Dockerfile:313-323`) and becomes
what the node reports over the gRPC `Version()` API. tuppr decides whether a node needs
upgrading by comparing that string (`upgrade.go:676`, `:905`) and never looks at the
image ref — so a kernel bump that left the Talos version unchanged would be **invisible**
to tuppr. Encoding the kernel in the suffix is what makes kernel rollouts automatic.
The suffix satisfies tuppr's CRD pattern `^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9\-\.]+)?$`.

## Per-bump maintenance

Renovate opens a PR bumping `ARG KERNEL_VERSION`. The tarball is verified by Greg KH's
committed PGP key, so there is no second field to update. Then rebuild and read the log:

| surface | measured 6.18.44 -> 7.1.9 (two minors) | typical single minor |
|---|---|---|
| `olddefconfig` delta | +213 / -154 | ~+70 / -55 |
| `hack/modules-amd64.txt` reconciliation | 3 entries | 0-2 |

The module list is a hard gate: talos `Dockerfile:687` runs
`xargs -a modules-amd64.txt -I {} install -D usr/lib/modules/$KERNELRELEASE/{}`, which
fails the build on any listed module the config did not produce. The 7.1.9 reconciliation
was `kernel/crypto/xor.ko` -> `kernel/lib/raid/xor/xor.ko` (moved), and dropping
`kernel/crypto/hkdf.ko` (`CONFIG_CRYPTO_HKDF` deleted upstream) and
`kernel/drivers/watchdog/iTCO_vendor_support.ko` (removed in 7.0).

## What is deliberately not carried

**siderolabs' 6 kernel patches.** On 7.1.9 only `0002` and `0003` still applied, and both
are Cadence/Atmel MACB ethernet backports — `CONFIG_MACB` is not set in this config and
these nodes are `r8169`, so the entire set changed nothing in the built kernel while
costing a triage pass every bump. `0004` (PCI bridge window), `0006` (page_table_check)
and `0007` (tun dst unclone) do touch enabled subsystems but no longer apply, which for
backports usually means the fix is already upstream. Re-add individually if a real need
appears; do not re-vendor the set wholesale.

**The kernel config and signing key.** Fetched from `siderolabs/pkgs` at `PKGS_SHA`
(`f541ca4`, the `PKGS` pin in talos v1.13.9 `Makefile:31`) rather than committed. They are
upstream files this repo does not modify, and fetching them means they track the Talos
version being built instead of needing a manual refresh every minor. Bump `PKGS_SHA` and
`TOOLS_REV` together when moving to a new Talos release.

**A distro toolchain.** The builder is `ghcr.io/siderolabs/{tools,llvm}` at `TOOLS_REV` —
the same images pkgs builds with — so the compiler is upstream's exact clang, pinned to a
ref this repo controls. A distro base would drift its clang independently of anything here,
which matters because `CONFIG_LTO_CLANG_THIN=y` makes the compiler load-bearing.

## Build

`just talos kernel-build <kernel-version> <talos-version>` runs the whole pipeline.
Requires ~25 GB free disk and takes roughly 15 min on 24 cores; the kernel compile
dominates and is fully unattended.

## Rollout

The first hop off the Image Factory is manual, once per node. tuppr's
`buildTalosUpgradeImage` errors when the config's install image does not embed the
schematic the running node reports (`upgrade.go:830`), which is exactly what happens the
moment a node's config points at this registry while it is still booted on a Factory
image:

```sh
talosctl -n <ip> upgrade --image ghcr.io/tanguille/installer:<version> --reboot-mode powercycle
```

Do one node at a time and confirm `etcd` rejoins between each — the fleet is three
control-plane nodes and etcd tolerates exactly one down. After every node runs a locally
imaged build, no node reports a `schematic` extension, both of tuppr's guards become
unreachable, and it degenerates to pure tag substitution — from then on a Renovate bump
plus a merge rolls the fleet with no manual step.
