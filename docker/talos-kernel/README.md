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

### What else 7.x changes here

Measured on control-2 (the only 7.1.9 node) against control-3, its identical twin on 6.18.44.
Everything gfx12/ROCm-related is **not** in this list: the R9700 lives on control-1, which is
still on 6.18.44, and control-2's iGPU group gets `/dev/dri` without `/dev/kfd`, so amdkfd is
unreachable there regardless.

| change | status on control-2 |
|---|---|
| Preemption `none` -> `full` | **Live, unintended, probably benign.** `Dynamic Preempt: full` vs `none` on control-3. 7.0 made `PREEMPT_NONE` depend on `ARCH_NO_PREEMPT`, so `olddefconfig` took the new default. Full preemption trades a few percent of throughput for better tail latency, which suits etcd and Ceph OSDs, so the direction is fine — the problem is that nobody chose it and control-2 now schedules unlike its twin, confounding any cross-node latency comparison. `CONFIG_PREEMPT_DYNAMIC=y`, so `preempt=` on the cmdline settles it without a rebuild; control-1's schematic already pins `preempt=voluntary`. Pinning it in `talos/schematic.yaml` too would make a future `olddefconfig` unable to move it — at the cost of changing the schematic id, and therefore the installer repo path. |
| GTT visible to the memory subsystem (`NR_GPU_ACTIVE`) | **Live in `/proc/meminfo`** (~1.4 GiB), the first-party fix for the iGPU GTT leak that is invisible to `kubectl top`. **Not yet exported** — node-exporter v1.12.1 has no `GPUActive` collector, so it needs a bump or a textfile shim before it can be alerted on. |
| eBPF verifier state pruning (7.0/7.1) | Applies. Upstream's veristat numbers are measured on Cilium's own objects (`bpf_lxc.o` `tail_ipv4_ct_egress` -44%). Not re-measured here. |
| HRTICK / HRTICK_DL default on | Applies; `CONFIG_HRTIMER_REARM_DEFERRED=y` in the built config. EEVDF slice enforcement moves off the 4 ms tick. |
| r8169 LTR enabled for RTL8125 (7.0) | **Watch item, not a gain.** control-2's `eno1`/`enp2s0` are both RTL8125B in `bond0`. New PCIe power-management behaviour on that node's only NICs; first bisect candidate for latency spikes or OSD heartbeat timeouts. |

The Ceph `aes256k` feature this was built for is **not yet in use** — it also needs Rook's
`spec.security.cephx.allowedCiphers`, which cannot be set while any node is below 7.0.

### The counterweight: 7.x is not LTS

| | 6.18 | 7.1 |
|---|---|---|
| kernel.org moniker | **longterm** | stable |
| Projected EOL | Dec 2028 | none published; 7.2 shipped 2026-08-16 |
| `siderolabs/pkgs` | `release-1.13` and `release-1.14` both pin 6.18.44 | never shipped |

Measured series lifetimes: 6.19.y made its last release 9 days after 7.0 shipped, 7.0.y 13
days after 7.1. 7.2 is already out, so 7.1.y is likely within weeks of EOL. Mainline cadence
this year was ~63 days per series, so tracking `stable` means a full series migration roughly
every 9 weeks, on top of a Talos rebase every ~4 months, with no upstream test coverage for
the combination.

That is the trade: this closes a residual client-key exposure that Rook itself treats as a
supported configuration (its shipped `cluster.yaml` documents `keyType: aes` as the correct
setting for nodes below 7.0), and buys it with a permanent local kernel treadmill. Switching
the Renovate datasource filter to `moniker="longterm"` once a 7.x LTS exists is the exit.

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

Tag installers `v<talos>-k<kernel>`, e.g. `v1.13.9-k7.1.9`, and publish them under the
schematic id: `ghcr.io/tanguille/installer/<schematic-id>:v<talos>-k<kernel>`.

`TAG` is embedded into `pkg/machinery/gendata` (talos `Dockerfile:313-323`) and becomes what
the node reports over the gRPC `Version()` API. tuppr reads exactly that (`client.go:161`
returns `version.GetTag()`) and compares it to its target with plain string inequality
(`upgrade.go` `nodeNeedsUpgrade`). It never looks at the image ref, so a kernel bump that left
the Talos version unchanged would be **invisible** to tuppr. Encoding the kernel in the suffix
is what makes a kernel-only rollout visible at all.

The id belongs in the **repo path, not the tag**. `buildTalosUpgradeImage` rebuilds the target
as `<repo>:<targetVersion>` after `strings.Cut(currentImage, ":")` discards the current tag
wholesale, so a per-node tag suffix names an image tuppr can never request. Under the id the
ref matches what tuppr's `factory-url` mode composes anyway (`<base>/<schematic>:<version>`),
and the two nodes sharing `talos/schematic.yaml` share one artifact.

The suffix satisfies tuppr's CRD pattern `^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9\-\.]+)?$`.

## Per-bump maintenance

Renovate opens a PR bumping `ARG KERNEL_VERSION`. The tarball is verified by Greg KH's
committed PGP key, so there is no second field to update. Then rebuild and read the log:

| surface | measured, 6.18.44 -> 7.1.9 |
|---|---|
| `olddefconfig` delta | +206 / -149 |
| `hack/modules-amd64.txt` reconciliation | 3 entries |

The delta is not cosmetic: the 6.18.44 -> 7.1.9 one flipped `CONFIG_PREEMPT_NONE` to
`CONFIG_PREEMPT`, changing the scheduling profile of a Ceph and etcd node without anyone
choosing it. Read it, and pin anything load-bearing on the cmdline rather than relying on a
compiled-in default that upstream can move.

That is one measurement spanning three feature releases (6.19, 7.0, 7.1); a single-minor bump
has not been measured yet, so treat it as an upper bound rather than a per-bump expectation.

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

`just talos kernel-build <node>...` runs the whole pipeline. Both versions are derived, not
typed: the kernel from `ARG KERNEL_VERSION` in the Dockerfile (so a Renovate bump is what
changes the build) and Talos from the tuppr CR.
Requires ~25 GB free disk and takes roughly 15 min on 24 cores; the kernel compile
dominates and is fully unattended.

## Rollout

The first hop off the Image Factory is manual, once per node. tuppr's
`buildTalosUpgradeImage` errors when the config's install image does not embed the schematic
the running node reports (`upgrade.go`), which is what happens the moment a node's config
points at this registry while it is still booted on a Factory image:

```sh
talosctl -n <ip> upgrade --image ghcr.io/tanguille/installer/<schematic-id>:<version> \
    --reboot-mode powercycle
```

Do one node at a time and confirm `etcd` rejoins between each — the fleet is three
control-plane nodes and etcd tolerates exactly one down.

### The rollout is not self-closing yet

Booting a node off a locally imaged installer does **not** finish the job, and the failure is
silent. A locally imaged node reports no `schematic` extension, and `looksLikeGenericInstaller`
matches only `ghcr.io/siderolabs/installer`, so both of tuppr's protective guards go
unreachable and it falls through to bare `<repo>:<targetVersion>` substitution. `<repo>` comes
from the node's **own `.machine.install.image`**. Leave that pointing at
`factory.talos.dev/installer/<id>` and the next Talos bump quietly reinstalls the stock
kernel — no error, because the guard that would have raised one no longer applies.

Verified on control-2 on 2026-08-20: booted on `v1.13.9-k7.1.9`, kernel `7.1.9-talos`, while
its `.machine.install.image` was still `factory.talos.dev/installer/1fd419f5…:v1.13.9`. tuppr
reported "All nodes are up to date" only because `findNextNodes` skips every node listed in
`Status.CompletedNodes` before it ever compares versions; that list resets when the CR
generation changes, i.e. on the next Talos bump.

Two things must therefore land together with the first node's cutover, and neither is in this
change:

1. **`.machine.install.image` repointed** to `ghcr.io/tanguille/installer/<schematic-id>`, or
   the node annotated with `tuppr.home-operations.com/factory-url` +
   `tuppr.home-operations.com/schematic`. The annotation route is the one tuppr documents for
   a self-hosted factory and is the only one that works when runtime reports no schematic.
2. **A single composed version string.** tuppr compares against one value, so
   `spec.talos.version` has to read `v1.13.9-k7.1.9` — but that field carries
   `# renovate: datasource=github-releases depName=siderolabs/talos`, which rewrites it to
   `v1.13.10` and eats the kernel suffix. Two Renovate inputs have to compose into one string
   and nothing composes them yet. Until that is solved, a kernel bump is a manual edit of
   `spec.talos.version` (or a per-node `tuppr.home-operations.com/version` annotation, which
   takes precedence over the CR).

Until both are done, treat every Talos bump as "re-run the build and re-cut the affected
nodes by hand", and keep at least one node on stock so a bad kernel cannot take the fleet.
