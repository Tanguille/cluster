# Talos kernel package

Builds a Talos-compatible Linux kernel package so the cluster can run a newer kernel
than Talos ships. Talos v1.14.0 ships **Linux 6.18.48**; this tracks kernel.org
**stable** (7.1.13 at time of writing).

Neither `siderolabs/talos` nor `siderolabs/pkgs` is forked. Both are consumed at their
release tags and steered with make variables.

## Why

The in-kernel Ceph client gained AES256-KRB5 (`aes256k`) support in **Linux 7.0**
(`b7cc142dbafe libceph: add support for CEPH_CRYPTO_AES256KRB5`, merged in
`ceph-for-7.0-rc1`). It is absent from 6.18.48 and was **not** backported to 6.18.y — it
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

| change                                                | status on control-2                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
|-------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Preemption `none` -> `full`                           | **Live, unintended, probably benign.** `Dynamic Preempt: full` vs `none` on control-3. 7.0 made `PREEMPT_NONE` depend on `ARCH_NO_PREEMPT`, so `olddefconfig` took the new default. Full preemption trades a few percent of throughput for better tail latency, which suits etcd and Ceph OSDs, so the direction is fine — the problem is that nobody chose it and control-2 now schedules unlike its twin, confounding any cross-node latency comparison. `CONFIG_PREEMPT_DYNAMIC=y`, so `preempt=` on the cmdline settles it without a rebuild; control-1's schematic already pins `preempt=voluntary`. Pinning it in `talos/schematic.yaml` too would make a future `olddefconfig` unable to move it — at the cost of changing the schematic id, and therefore the installer repo path. |
| GTT visible to the memory subsystem (`NR_GPU_ACTIVE`) | **Live in `/proc/meminfo`** (~1.4 GiB), the first-party fix for the iGPU GTT leak that is invisible to `kubectl top`. **Not yet exported** — node-exporter v1.12.1 has no `GPUActive` collector, so it needs a bump or a textfile shim before it can be alerted on.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| eBPF verifier state pruning (7.0/7.1)                 | Applies. Upstream's veristat numbers are measured on Cilium's own objects (`bpf_lxc.o` `tail_ipv4_ct_egress` -44%). Not re-measured here.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| HRTICK / HRTICK_DL default on                         | Applies; `CONFIG_HRTIMER_REARM_DEFERRED=y` in the built config. EEVDF slice enforcement moves off the 4 ms tick.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| r8169 LTR enabled for RTL8125 (7.0)                   | **Not applicable on this hardware.** Looked like the main regression risk (both NICs are RTL8125B in `bond0`), but ACPI `_OSC` on both Chuwi boxes reports `platform does not support [AER LTR DPC]` and the OS only gets `[PCIeHotplug PME PCIeCapability]`, so the firmware never hands LTR to the kernel and the commit cannot engage. Same `_OSC` line on control-3, so this is a property of the box, not of 7.x. It also means AER reporting is unavailable, i.e. PCIe correctable/uncorrectable errors are invisible here by construction — do not write alerts against AER on these nodes.                                                                                                                                                                                         |

The Ceph `aes256k` feature this was built for is **not yet in use** — it also needs Rook's
`spec.security.cephx.allowedCiphers`, which cannot be set while any node is below 7.0.

### The counterweight: 7.x is not LTS

|                    | 6.18                                               | 7.1                                    |
|--------------------|----------------------------------------------------|----------------------------------------|
| kernel.org moniker | **longterm**                                       | stable                                 |
| Projected EOL      | Dec 2028                                           | none published; 7.2 shipped 2026-08-16 |
| `siderolabs/pkgs`  | `release-1.14` pins 6.18.48                        | never shipped                          |

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

```text
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

| image                                   | schematic                  | nodes                |
|-----------------------------------------|----------------------------|----------------------|
| `ghcr.io/tanguille/installer/shared`    | `talos/schematic.yaml`     | control-2, control-3 |
| `ghcr.io/tanguille/installer/control-1` | `control-1.schematic.yaml` | control-1            |

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
`<firmware-date>-<extensions-tag>` (e.g. `20260810-v1.14.0-rc.2`), *not* as the pipeline's
`v<talos>-k<kernel>`. The build discovers the published tag rather than assuming it; do not
hardcode one.

## Per-bump maintenance

Build from the bump branch **before** merging it:

```sh
branch=renovate/linux-7.x
gh workflow run talos-kernel.yaml --ref "${branch}"
# Dispatch returns immediately and the build is ~2h45m, so watch it rather than merging on the
# assumption it worked. --exit-status is what makes a failed build a failed command; without it
# `gh run watch` exits 0 whatever the run concluded. Filtered by branch AND event so a push
# build of main cannot be picked up as this one.
gh run watch --exit-status "$(gh run list --workflow talos-kernel.yaml --branch "${branch}" \
    --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId')"
```

The push trigger builds on merge, but the build takes ~2h45m and `pinned` becomes
`v<talos>-k<new-kernel>` the moment the merge lands — so between the two, every rendered config
names an installer tag that does not exist yet. Dispatching on the branch closes that window:
the workflow checks out that ref, so it builds that branch's `ARG KERNEL_VERSION` against that
branch's tuppr CR, and the merge only ratifies an image that is already published.

Renovate opens a PR bumping `ARG KERNEL_VERSION`. The tarball is verified by Greg KH's
committed PGP key, so there is no second field to update. The key's fingerprint was
cross-checked against the one kernel.org publishes on <https://www.kernel.org/signature.html>:

```text
committed:   647F28654894E3BD457199BE38DBBDC86092693E
kernel.org:  647F 2865 4894 E3BD 4571  99BE 38DB BDC8 6092 693E
```

Re-check it with `gpg --show-keys --with-fingerprint` if the key is ever replaced. Then rebuild and read the log:

| surface                                 | 6.18.44 -> 7.1.9                           | 7.1.10 -> 7.2.2         |
|-----------------------------------------|--------------------------------------------|-------------------------|
| `olddefconfig` delta                    | +206 / -149                                | +241 / -164             |
| `hack/modules-amd64.txt` reconciliation | 3 entries (1.13) / 3 + 1 dependency (1.14) | 5 entries + 3 (1.14)    |

The delta is not cosmetic: the 6.18.44 -> 7.1.9 one flipped `CONFIG_PREEMPT_NONE` to
`CONFIG_PREEMPT`, changing the scheduling profile of a Ceph and etcd node without anyone
choosing it. Read it, and pin anything load-bearing on the cmdline rather than relying on a
compiled-in default that upstream can move.

The two columns bracket the range: the first spans three feature releases (6.19, 7.0, 7.1), the
second is a single minor. They are close, so the delta tracks the series handover rather than
the number of releases crossed — a minor bump is not the cheap case.

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

### Hold: 7.2 is blocked on Cilium, do not merge the bump

**A green build does not mean a bootable fleet.** 7.2.2 built, published and booted cleanly;
the node was still lost, because the break is in userspace and the pipeline cannot see it.

Cilium's feature probe passes a *pointer* to `bpf_set_retval`, which has always taken an
integer. The kernel tolerated it until [`b1f7f67b74c2e`][k-commit] ("bpf: Add validation for
bpf_set_retval argument") landed in **7.2-rc1**. The agent now dies at startup and never writes
a CNI config, so the node stays `NotReady` with no network:

```text
level=fatal msg="failed to probe helper"
  error="detect support for FnSetRetval for program type CGroupSock: load program:
         invalid argument: 0: (85) call bpf_set_retval#187: R1 is not a scalar"
  progType=CGroupSock helper=FnSetRetval
```

Tracked as [cilium/cilium#48016][issue]. It is a Cilium bug the kernel exposed, not a kernel
regression, and it is **not** version-specific to our 1.20: upstream reports 1.18, 1.19, 1.20
and 1.21.0-pre.0 all failing on 7.2 while the same builds run fine on 7.1.

The fix is [`67c619c`][fix] (probe via `bpf_core_enum_value_exists()` instead of emitting the
call). Re-measured on 2026-09-03: still present on the `v1.20` branch as `b73ca6e8d`
(2026-08-28), absent from `v1.19` and `v1.18`, and still in **no release** — 1.20.1 remains the
latest and shipped 2026-08-18, ten days before the backport. Re-check with:

```sh
gh api "repos/cilium/cilium/commits?sha=v1.20&per_page=100" \
    -q '[.[] | select(.commit.message | test("HAVE_SET_RETVAL"))] | length'
gh release list --repo cilium/cilium --limit 5
```

**Lift the hold when a Cilium release containing that commit is deployed here** — 1.20.2 or
later. Until then `ARG KERNEL_VERSION` stays on 7.1.y and Renovate's 7.2.x PR stays open and
unmerged; the open PR is the reminder. It is deliberately not pinned via `allowedVersions`:
combined with the `iseol=false` filter in `.renovaterc.json5`, a `<7.2` bound empties the feed
once 7.1 leaves `moniker=stable`, and bumps then stop **silently** — which is worse than a PR
someone has to decline. The cost of the hold is that 7.1.y patch bumps stop too, since Renovate
only ever offers the highest stable.

[k-commit]: https://github.com/torvalds/linux/commit/b1f7f67b74c2e
[issue]: https://github.com/cilium/cilium/issues/48016
[fix]: https://github.com/cilium/cilium/commit/67c619cb0a43c7178bf843c9281fc77fe64fe13f

### Rolling a bump back

Revert the PR. `spec.talos.version` goes back to the older string and tuppr rolls each node to
it — that is a live downgrade command, not a no-op, so for a Talos *minor* revert suspend the CR
first and decide deliberately whether the Kubernetes and CNPG state tolerates going backwards.
The older installer tag is still published; tags in `ghcr.io/tanguille/installer/*` are never
reused.

To pull a single node back out of band, without waiting for tuppr:

```sh
talosctl -n <ip> upgrade -i ghcr.io/tanguille/installer/<schematic>:<old-version> \
    -m powercycle --timeout=15m
# ...then pin it there, or tuppr will roll it forward again on the next reconcile:
kubectl annotate node <node> tuppr.home-operations.com/version=<old-version>
kubectl get nodes -o custom-columns=NAME:.metadata.name,\
RUNNING:'.status.nodeInfo.osImage',HOLD:'.metadata.annotations.tuppr\.home-operations\.com/version'
```

`getTargetVersion` still prefers a node annotation over the CR, and since the annotation is no
longer part of machine config, Talos does not re-enforce or clear it. `kubectl annotate node
<node> tuppr.home-operations.com/version-` releases the hold. The `HOLD` column is empty on a
normally-managed node — a non-empty value means that node is pinned and is NOT tracking the CR.

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

`etcd` is not a sufficient check. Every Talos service can report `Running/OK` on a node that
has no working network: the first node on 7.2.2 did exactly that, with `cilium` in
`CrashLoopBackOff` and `Ready=False / cni plugin not initialized`. Wait for the node to reach
`Ready` and confirm its agent pod is up before touching the next one:

```sh
kubectl get node <node>
kubectl -n kube-system get pods -o wide | grep "cilium.*<node>"
```

### The rollout is not self-closing

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

Two things have to be true for a node. All three nodes satisfy both as of 2026-08-21:

1. **`.machine.install.image` repointed** to `ghcr.io/tanguille/installer/<schematic>`. Left on
   `factory.talos.dev`, tuppr's bare `<repo>:<targetVersion>` substitution silently reinstalls
   the stock kernel.
2. **A version string tuppr will actually ask for.** It compares one value, so the node has to
   advertise `v<talos>-k<kernel>` — which it does, because the installer is built with
   `TAG="${VERSION}"`. That string now lives in `spec.talos.version` itself.

   It used to live in a per-node `machine.nodeAnnotations."tuppr.home-operations.com/version"`,
   because the CR field was Renovate-managed by an inline annotation that captures the whole
   value and would rewrite `v1.13.9-k7.1.9` to `v1.13.10`, eating the suffix. That is a property
   of the *manager*, not the field: two file-scoped regex managers in `.renovaterc.json5` now own
   one half each, so the field can carry it. See `docs/tuppr-cr-version-target-plan.md`.

   The annotation mattered because only `just talos apply-node` could change it, so merging a
   bump PR rolled nothing. With the target in the CR, Flux carries it and a merge is the whole
   procedure.

To hold one node back while the rest move, use `spec.nodeSelector` on the CR — it is a full
`LabelSelector`, so `kubernetes.io/hostname NotIn [control-1]` parks that node declaratively, in
the same file as the version and under review like any other change. `getTargetVersion` still
prefers a node annotation over the CR (`upgrade.go:855`) and Talos no longer owns that key, so
`kubectl annotate node <node> tuppr.home-operations.com/version=<string>` is the imperative
fallback when you want a hold that leaves no diff — see "Rolling a bump back".

**A third thing is true for the CR.** Once a node reports `v<talos>-k<kernel>`, tuppr derives the
talosctl job image tag from that same string and pulls
`ghcr.io/siderolabs/talosctl:v1.13.9-k7.1.9`, which siderolabs never publishes — the job hangs in
ImagePullBackOff until `policy.timeout`. Measured on control-2 on 2026-08-21. `spec.talosctl.image.tag`
is pinned to the plain upstream version to stop it. It only bites on the *second* roll of a node,
because the first still has an unsuffixed version at the moment the job is built.

Since every node now runs a custom kernel, no node is left on stock as a fallback. A Talos bump is
merge-only: the push-to-main build publishes the installer, and tuppr rolls the fleet once the CR
target and the published tag agree. `just talos upgrade-node <node> <ip>` remains for cutting a
node by hand; it reads the pinned image straight out of the rendered config.
