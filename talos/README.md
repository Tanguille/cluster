# Talos

Declarative [Talos Linux](https://www.talos.dev) machine configuration, built from composable
multi-document layers. Nothing here is applied automatically; configs are rendered on demand and
pushed to nodes with `talosctl`.

## Why not talhelper

talhelper cannot express Talos 1.14, so this directory renders configs with `talosctl` directly.

Both talhelper `master` and `v3.1.16` pin machinery `v1.14.0-alpha.2`. Rebuilt against `rc.1` it
panics under the 1.14 version contract: machinery sets `MachineInstall` to `nil` (replaced by
`UnattendedInstallConfig`) and talhelper dereferences it unguarded. Staying on the 1.13 contract
instead leaves the whole `Kube*` document family and `UnattendedInstallConfig` unreachable, because
they are mutually exclusive with the v1alpha1 base talhelper emits.

`talosctl machineconfig patch` generates no competing v1alpha1 base, so every document kind is
reachable. The layer model below follows [onedr0p/home-ops](https://github.com/onedr0p/home-ops),
with SOPS in place of 1Password.

## Layout

| Path                                    | Purpose                                                                   |
| --------------------------------------- | ------------------------------------------------------------------------- |
| `cluster.yaml.j2`                       | Documents applied to every node                                           |
| `controlplane.yaml.j2`                  | Control-plane-only documents, including `machine.type`                    |
| `workers.yaml.j2`                       | Worker-only documents (does not exist yet; created with the first worker) |
| `nodes/<role>/<node>.yaml.j2`           | Per-node documents (hostname, address, MAC selector, install disk, labels)|
| `nodes/<role>/<node>.schematic.yaml`    | Optional per-node schematic override                                      |
| `schematic.yaml`                        | Shared [Image Factory](https://factory.talos.dev) schematic               |
| `talsecret.sops.yaml`                   | SOPS-encrypted secrets bundle (native `talosctl` format)                  |
| `mod.just`                              | Recipes (`just talos ...`)                                                |

## Rendering

`just talos render-config <node>` builds the final machine config in three layers. Conceptually,
where `<role>` is `controlplane` or `workers`, chosen by which directory holds the node file:

```text
     cluster.yaml.j2          every node
  +  <role>.yaml.j2           role layer, sets machine.type
  +  nodes/<role>/<node>.yaml.j2
  =  the node's machine config
```

The executable form is in `talos/mod.just`: each layer is rendered by `just template` and the results
are merged by `talosctl machineconfig patch`, the first as the base and the rest as `-p @` patches.
Later patches strategically merge into earlier ones: maps deep-merge, lists replace, and documents
with the same kind/name merge.

Two conventions keep the layers honest:

- **Directory placement is the single source of truth for a node's role.** The role layer is chosen
  by which `nodes/<role>/` directory contains the node file, and `machine.type` is set by the role
  layer, not the node file. A node cannot claim one role by filename and another by content.
- **Secrets never appear in this repo in plaintext.** `talsecret.sops.yaml` is decrypted at render
  time and handed to minijinja as the *template context*, so templates reference its own key names
  (`{{ certs.os.crt }}`, `{{ trustdinfo.token }}`). Nothing is written to disk.

Talos and Kubernetes versions are not hardcoded. The root `template` recipe reads them from the tuppr
CRs (`kubernetes/apps/system-upgrade/tuppr/upgrades/`), so Renovate keeps managing them in one place.
`vip` and `gateway` are defined once in `mod.just` and passed to every layer alongside the node's
schematic id, so each address has a single definition rather than one per file that references it.

Documents are laid out to keep `diff-node` honest: `talosctl` diffs a config **textually**, so moving
a document between layers reorders the output stream and reads as a change even when the content is
byte-identical. Content shared by every node (the installer image) lives in the layer that owns it;
documents that are identical per node but would reorder the stream stay put.

## Schematics

`just talos schematic-id <node>` POSTs the schematic to the Image Factory and returns its
content-addressed ID, which is templated into the installer image.

Resolution is per node: `nodes/<role>/<node>.schematic.yaml` wins when present, otherwise
`schematic.yaml` applies. Overrides are complete files, not deltas. Today only `control-1`
overrides, because it is the TrueNAS VM and the only dGPU host.

Schematics are plain YAML, deliberately not templates. Routing them through `template` would
decrypt the secrets bundle to render a file that references no secrets, on the hot path of nearly
every `just talos` command. If a schematic ever needs a variable, add the extension back.

**The ID is content-addressed, so any edit to a schematic moves it** — including a one-character
change to `extraKernelArgs`. Every installer reference derived from that ID moves with it. For nodes
pointing at the Image Factory that is invisible and self-healing, because the Factory builds the new
ID on demand. It is *not* self-healing for any node whose installer is mirrored to another registry
under the schematic path: that mirror must be republished under the new ID first, or the next upgrade
fails to pull. Check which nodes use a non-Factory installer before changing a schematic.

## Gotchas

- `machine.ca` and `cluster.ca` merge as a cert+key **unit**: a layer supplying only `key` blanks
  `crt`. This is why `controlplane.yaml.j2` repeats the `crt` alongside the keys.
- `minijinja-cli` must run with `--autoescape=none`. The default JSON-escapes every substitution,
  which silently wraps certs and versions in quotes and produces a config that looks right and is not.
- `talsecret.sops.yaml` is the native `talosctl` secrets bundle, not a talhelper format. `talosctl gen
  config --with-secrets` consumes it directly. Do not rename its keys; the templates and
  `just talos talosconfig` both depend on them.

## Talos 1.14 adoption

The version lives in the tuppr CR, not here. Once the cluster is on 1.14, these documents become
available and each should land as its own change with a `diff-node` review. Swept and confirmed
renderable against this pipeline:

| Document | Replaces / adds |
| --- | --- |
| `CRICustomizationConfig` | `machine.files` CRI drop-in; no reboot needed to change CRI config |
| `FilesystemTrimConfig` | absent on upgraded clusters, so trimming is off by default |
| `SecurityProfileConfig` | `workloadIsolation: true` (sandboxd). Check first: no in-tree iSCSI volume plugin. Ceph CSI is unaffected |
| `SysctlConfig` / `SysfsConfig` / `KernelModuleConfig` / `UdevRulesConfig` | the deprecated `machine.*` equivalents |
| `KubeletConfig` + `KubeNodeConfig` | most of the kubelet block and the node labels |
| `FilesystemScrubConfig` | XFS scrub; note control-3's XFS shutdown history |
| `EtcFileConfig`, `RAIDArrayConfig`, `LVM*Config`, `BGPInstanceConfig`, `VethConfig` | new capabilities, none currently needed |

`VolumeConfig`'s `filesystem.xfs.minAllocationGroupSize` only affects volumes Talos formats, so it
is a wipe-time decision rather than a live one.

Evaluate separately rather than adopting blindly: NRI is enabled by default in 1.14,
`net.ipv4.conf.*.send_redirects` defaults to `0`, and etcd's HTTP endpoints move `2379` → `2383`
(we are unaffected, `listen-metrics-urls` is pinned to `2381`, but re-verify the scrape).

## Common tasks

These use the repo's pinned `talosctl` and `minijinja-cli`. `.envrc` puts them on `PATH` via mise,
so the bare commands below are the pinned ones. Without direnv, add the shims
(`export PATH="$HOME/.local/share/mise/shims:$PATH"`) or prefix with `mise exec --`; a `talosctl`
picked up from the system `PATH` is a different version than this pipeline is tested against.

```sh
just talos render-config <node>          # render a node's full machine config to stdout
just talos diff-node <node> <ip>         # dry-run the rendered config against the running node
just talos apply-node <node> <ip>        # render and apply
just talos upgrade-node <node> <ip>      # upgrade Talos using the node's schematic image
just talos upgrade-k8s                   # upgrade Kubernetes to the version in the tuppr CR
just talos talosconfig                   # regenerate the client config from the secrets bundle
just talos download-image <node> <ver>   # fetch a metal ISO from the Image Factory
```

Verify any refactor of these templates by running `diff-node` against **every** node and confirming
each reports `No changes.` before applying anything.
