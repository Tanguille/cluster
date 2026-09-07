# Talos

Declarative [Talos Linux](https://www.talos.dev) machine configuration, assembled by
[topf](https://postfinance.github.io/topf/) from `topf.yaml` plus the strategic-merge patches in
this directory. Nothing here is applied automatically; configs are rendered on demand and pushed
to nodes with `just talos apply`.

## Why topf

talhelper cannot express Talos 1.14: both `master` and `v3.1.16` pin machinery `v1.14.0-alpha.2`,
and rebuilt against a 1.14 release it panics under the version contract (machinery sets
`MachineInstall` to `nil`, replaced by `UnattendedInstallConfig`, and talhelper dereferences it
unguarded).

This directory previously rendered configs with `minijinja` + `talosctl machineconfig patch`,
deliberately generating no v1alpha1 base so that every 1.14 document kind stayed reachable. topf
does the same job with a real base: it reads `talsecret.sops.yaml` directly, generates the full
1.14 multi-document config from it, and merges these patches on top. That deletes the whole
secret-templating layer — no minijinja, no piping decrypted secrets into a renderer, no
hand-written CA/token plumbing.

The layout follows [onedr0p/cluster-template](https://github.com/onedr0p/cluster-template), which
made the same move in `a25d0ab`, with SOPS in place of 1Password.

## Layout

| Path                          | Purpose                                                          |
| ----------------------------- | ---------------------------------------------------------------- |
| `topf.yaml`                   | Node inventory, cluster endpoint, versions, shared template data  |
| `all/`                        | Patches applied to every node                                     |
| `control-plane/`              | Control-plane-only patches                                        |
| `node/<host>/`                | Per-node patches (`node/`, singular — not `nodes/`)               |
| `schematic.yaml`              | Shared [Image Factory](https://factory.talos.dev) schematic       |
| `control-1.schematic.yaml`    | Per-node schematic override                                       |
| `talsecret.sops.yaml`         | SOPS-encrypted secrets bundle (native `talosctl gen secrets`)     |
| `mod.just`                    | Recipes (`just talos ...`)                                        |
| `rendered/`                   | `just talos render` output — gitignored, contains certificates    |

Patches merge in the order `all/` → `control-plane/` → `node/<host>/`, lexicographically within
each directory, later winning. Files ending `.yaml.tpl` are Go templates rendered per node;
plain `.yaml` files are not. The template context is documented in
[topf's configuration model](https://postfinance.github.io/topf/main/configuration-model/) —
`{{ .Node.Host }}`, `{{ .Node.IP }}`, `{{ .Node.Data.x }}`, `{{ .Data.x }}`,
`{{ .KubernetesVersion }}`.

**Schematics must stay outside the patch directories.** topf loads every `.yaml` under `all/`,
`<role>/` and `node/<host>/` as a machine-config patch, so a schematic parked in one of them
would be merged into the config.

## What topf generates, and what these patches do

topf generates the entire Talos 1.14 document set from the secrets bundle: the CAs, tokens,
cluster identity, etcd encryption, service-account key, and every `Kube*Config`. The patches here
only cover what differs from its defaults, and several exist purely to hold behaviour flat:

| Patch                       | Why it is not a default                                              |
| --------------------------- | -------------------------------------------------------------------- |
| `all/00-install.yaml.tpl`   | topf's installer patch points at `factory.talos.dev` with a `/dev/sda` selector |
| `all/10-cluster.yaml.tpl`   | pod/service subnets default to `10.244.0.0/16` and `10.96.0.0/12`    |
| `all/21-network.yaml.tpl`   | `forwardKubeDNSToHost` defaults to **true**                          |
| `all/30-kubelet.yaml.tpl`   | deletes `KubeletConfig` to keep `machine.kubelet.extraMounts`         |
| `all/70-security.yaml`      | `workloadIsolation` defaults to **true**; PodSecurity admission is generated |
| `control-plane/00-cluster.yaml.tpl` | control-plane nodes are tainted `NoSchedule`, flannel and CoreDNS are on |

### The kubelet exception

`all/30-kubelet.yaml.tpl` deletes the generated `KubeletConfig` document and keeps the deprecated
v1alpha1 `machine.kubelet` instead. This is the one place the repo diverges from upstream, and it
is forced: `KubeletConfig` has five fields and `ExtraMounts()` is `return nil`
(machinery v1.14.0 `config/types/k8s/kubelet.go:189-192`), so the `/var/openebs/local` rshared
bind mount that openebs-localpv needs cannot be expressed. The two are mutually exclusive
("kubelet config is already set in v1alpha1 config"), so `$patch: delete` is the only way to keep
the mount. `talosctl validate -m metal` accepts the result.

Two ways out, neither taken here:

- Talos gives `KubeletConfig` a mounts field. Nothing to do but wait.
- Point openebs' `basePath` at a `UserVolumeConfig`-backed `/var/mnt/<name>`, which Talos'
  own local-storage guide now recommends and which needs no kubelet mount at all. Available
  today, but it repartitions a live boot disk and migrates the existing `/var/openebs/local`
  data — a storage change, not a tooling one, so it wants its own PR and its own rollback.

## Versions

`topf.yaml` names `talosVersion`, `kubernetesVersion` and `data.installerTag`. The tuppr CRs in
`kubernetes/apps/system-upgrade/tuppr/upgrades/` remain the **operational** source — tuppr is what
performs upgrades, and `.github/workflows/flate.yaml` reads `.spec.talosctl.image.tag`. The
annotations in `topf.yaml` are byte-identical to the CRs', so Renovate branches on the same
`depName`+datasource and moves both files in one PR; `just talos check-versions` fails CI if a
hand edit ever splits them.

`talosVersion` is the plain release (`v1.14.0`). The `-k<kernel>` composite belongs only in
`data.installerTag`: as a `github-releases` `currentValue` it resolves to no-result and would
freeze the dep silently.

## Installer images

Every node pins a custom-kernel installer from `ghcr.io/tanguille/installer/<schematic>`, not the
Image Factory — see `docker/talos-kernel/README.md`. `data.installer` in `topf.yaml` names the
repo, one per **schematic** (`shared` for `schematic.yaml`, else the node name). A per-node name
rather than the schematic id, because the id moves whenever a schematic is edited and each new id
is a fresh private ghcr package.

topf still generates its own `UnattendedInstallConfig` pointing at the Factory;
`all/00-install.yaml.tpl` overrides it. That patch must carry the image **and** the disk selector
in the same document: `cel.Expression.Merge` overwrites unconditionally, so an image-only
override would silently blank the selector and produce a config Talos rejects.

## Schematics

`just talos download-image <node> <ver>` resolves a schematic id by POSTing the file to the Image
Factory. It deliberately does not use `topf schematic-ids`, which at v0.6.0 ignores
`--nodes-filter` and prints every node's id, and which computes ids locally without *registering*
them — the ISO URL needs a schematic the Factory knows.

Local computation and the Factory POST agree today (verified for both schematics). The id is
content-addressed, so **any** change to a schematic's fields moves it, including one character of
`extraKernelArgs`; comments and formatting do not, because the Factory canonicalises before
hashing. Every installer reference derived from that id moves with it, which is not self-healing
for a node whose installer is mirrored under the schematic path.

## Gotchas

- **`topf upgrade` is unusable here and no recipe wraps it.** Its version extractor
  (`^.*/([a-zA-Z0-9]+):v?(.+)$`) rejects the hyphen in `installer/control-1`, and for
  `installer/shared` it reads `shared` as the schematic, which never equals the node's runtime
  id — so it reports "upgrade required" on every run, forever. With `--confirm=false` that is an
  unconditional reinstall-and-reboot loop. tuppr owns upgrades.
- **Never run `topf secrets` in this directory.** It writes to `secretsPath`. It only generates
  when no bundle is found, but the blast radius is the cluster's identity.
- `topf reset` cannot express the old `wipe=false`. `just talos reset-node` passes `--full=false`,
  which still wipes STATE and EPHEMERAL; topf's own default takes the whole disk.
- `just talos diff` exits 2 when there *is* a diff. The recipe absorbs that; a bare
  `topf apply --dry-run` in a script must too.
- Adding a node means adding it to `topf.yaml`, creating `node/<host>/`, and building its
  installer (`just talos kernel-build <host>`). There is no cluster-layer installer fallback that
  would work — the generated one points at the Factory and would install a stock kernel.

## Common tasks

These use the repo's pinned `topf` and `talosctl`. `.envrc` puts them on `PATH` via mise; without
direnv, add the shims (`export PATH="$HOME/.local/share/mise/shims:$PATH"`) or prefix with
`mise exec --`.

```sh
just talos render                  # render every node config to talos/rendered
just talos diff                    # what would change on the nodes
just talos diff --nodes-filter '^control-1$'
just talos apply                   # render and apply, with a per-node diff and prompt
just talos nodes                   # live node state
just talos talosconfig             # regenerate the client config
just talos check-versions          # topf.yaml vs the tuppr CRs
just talos upgrade-k8s             # Kubernetes, to the version in the tuppr CR
just talos download-image <node> <ver>
```

Verify any refactor of these patches by running `just talos diff` against **every** node before
applying anything.
