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
| r8169 LTR enabled for RTL8125 (7.0) | **Not applicable on this hardware.** Looked like the main regression risk (both NICs are RTL8125B in `bond0`), but ACPI `_OSC` on both Chuwi boxes reports `platform does not support [AER LTR DPC]` and the OS only gets `[PCIeHotplug PME PCIeCapability]`, so the firmware never hands LTR to the kernel and the commit cannot engage. Same `_OSC` line on control-3, so this is a property of the box, not of 7.x. It also means AER reporting is unavailable, i.e. PCIe correctable/uncorrectable errors are invisible here by construction — do not write alerts against AER on these nodes. |

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

Tag installers `v<talos>-k<kernel>`, e.g. `v1.13.9-k7.1.9`, and publish one per schematic:
`ghcr.io/tanguille/installer/shared` or `ghcr.io/tanguille/installer/<node>` for an override.

`TAG` is embedded into `pkg/machinery/gendata` (talos `Dockerfile:313-323`) and becomes what
the node reports over the gRPC `Version()` API. tuppr reads exactly that (`client.go:161`
returns `version.GetTag()`) and compares it to its target with plain string inequality
(`upgrade.go` `nodeNeedsUpgrade`). It never looks at the image ref, so a kernel bump that left
the Talos version unchanged would be **invisible** to tuppr. Encoding the kernel in the suffix
is what makes a kernel-only rollout visible at all.

The node belongs in the **repo path, not the tag**. `buildTalosUpgradeImage` rebuilds the
target as `<repo>:<targetVersion>` after `strings.Cut(currentImage, ":")` discards the current
tag wholesale, so a per-node tag suffix names an image tuppr can never request. Nodes sharing a
schematic still build only once — the second is a registry copy, not another imager run.

The suffix satisfies tuppr's CRD pattern `^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9\-\.]+)?$`.

## Two things that will bite

**GHCR package visibility.** New packages are **private** and there is no way to change that
default: GitHub's docs state a personal-account package "default visibility is private", and a
package inherits the linked repository's access permissions "but not the visibility". The
machine config carries no registry credentials, so a private package means the node cannot pull
its own installer — and each nested path is a separate package, so making one public covers none of the others.

Since the flip cannot be defaulted away, the naming minimises how many packages ever exist:
**one image per schematic, not per node.** Two schematics means two packages and two flips,
once, no matter how many nodes join.

| image | schematic | nodes |
|---|---|---|
| `ghcr.io/tanguille/installer/shared` | `talos/schematic.yaml` | control-2, control-3 |
| `ghcr.io/tanguille/installer/control-1` | `control-1.schematic.yaml` | control-1 |

Visibility is per package, so every later tag inherits it — there is nothing to do per kernel
bump, and a fourth node on the shared schematic needs no new package at all.

Not the schematic id, even though it is content-addressed: the id moves whenever a schematic is
edited, and each new id is a fresh private package, so a one-line `extraKernelArgs` change
would silently strand nodes on an unpullable ref.

The intermediates (`kernel`, `amdgpu`, `installer-base`, `imager`) stay private because only
the local build and the imager touch them.

Verify it the way a node would, not with bare `crane` — `crane digest` silently uses whatever
is in `~/.docker/config.json` and will happily report success on a private ref:

```sh
TOK=$(curl -s "https://ghcr.io/token?scope=repository%3A<repo>%3Apull&service=ghcr.io" | jq -r .token)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOK" \
    -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
    "https://ghcr.io/v2/<repo>/manifests/<tag>"     # 200 = a node can pull it
```

**The amdgpu extension names its own tag.** It publishes as
`<firmware-date>-<extensions-tag>` (e.g. `20260810-v1.14.0-rc.1`), *not* as the pipeline's
`v<talos>-k<kernel>`. The build discovers the published tag rather than assuming it; do not
hardcode one.

## Per-bump maintenance

Renovate opens a PR bumping `ARG KERNEL_VERSION`. The tarball is verified by Greg KH's
committed PGP key, so there is no second field to update. Then rebuild and read the log:

| surface | measured, 6.18.44 -> 7.1.9 |
|---|---|
| `olddefconfig` delta | +206 / -149 |
| `hack/modules-amd64.txt` reconciliation | 3 entries (1.13) / 3 + 1 dependency (1.14) |

The delta is not cosmetic: the 6.18.44 -> 7.1.9 one flipped `CONFIG_PREEMPT_NONE` to
`CONFIG_PREEMPT`, changing the scheduling profile of a Ceph and etcd node without anyone
choosing it. Read it, and pin anything load-bearing on the cmdline rather than relying on a
compiled-in default that upstream can move.

That is one measurement spanning three feature releases (6.19, 7.0, 7.1); a single-minor bump
has not been measured yet, so treat it as an upper bound rather than a per-bump expectation.

Talos **1.14 added a second gate**: `depmod --errsyms` must print nothing at all, so a listed
module whose dependency is absent now fails the build. 7.x split `stmmac_libpci.ko` out of
`stmmac-pci.ko`, which upstream's list (written against 6.18) does not carry — so the list is
closed over `modules.dep` rather than patched per split. Note 1.13 ships the same dangling
dependency; it simply has no gate to catch it.

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
talosctl -n <ip> upgrade --image ghcr.io/tanguille/installer/<node>:<version> \
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

Two things have to be true for a node, and both are now done for control-2:

1. **`.machine.install.image` repointed** to `ghcr.io/tanguille/installer/<schematic>`. Left on
   `factory.talos.dev`, tuppr's bare `<repo>:<targetVersion>` substitution silently reinstalls
   the stock kernel.
2. **A version string tuppr will actually ask for.** It compares one value, so the node has to
   advertise `v<talos>-k<kernel>`. `spec.talos.version` cannot carry it — that field is
   Renovate-managed against `siderolabs/talos` and would rewrite `v1.13.9-k7.1.9` to
   `v1.13.10`, eating the suffix. So the kernel half lives in a per-node
   `machine.nodeAnnotations."tuppr.home-operations.com/version"`, which `getTargetVersion`
   prefers over the CR (`upgrade.go:855`). control-1 and control-3 keep taking the plain
   version from `spec.talos.version` and still resolve against the Factory.

Still outstanding for the other two nodes: build their installers, repoint and annotate them
the same way, and flip each new ghcr package public. Until then treat a Talos bump as "re-run
the build and re-cut the affected nodes by hand", and keep at least one node on stock so a bad
kernel cannot take the fleet.
