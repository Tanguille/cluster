# Talos

Declarative [Talos Linux](https://www.talos.dev) machine configuration, built from composable
multi-document layers. Nothing here is applied automatically; configs are rendered on demand and
pushed to nodes with `talosctl`.

## Why not talhelper

talhelper cannot express Talos 1.14, so this directory renders configs with `talosctl` directly:
it emits a v1alpha1 base, which is mutually exclusive with the `Kube*` document family and
`UnattendedInstallConfig`.

`talosctl machineconfig patch` generates no competing v1alpha1 base, so every document kind is
reachable. The layer model below follows [onedr0p/home-ops](https://github.com/onedr0p/home-ops),
with SOPS in place of 1Password.

## Layout

| Path                                    | Purpose                                                                   |
| --------------------------------------- | ------------------------------------------------------------------------- |
| `cluster.yaml.j2`                       | Documents applied to every node                                           |
| `controlplane.yaml.j2`                  | Control-plane-only documents, including `machine.type`                    |
| `nodes/<role>/<node>.yaml.j2`           | Per-node documents (hostname, address, MAC selector, install disk, labels)|
| `nodes/<role>/<node>.schematic.yaml`    | Optional per-node schematic override                                      |
| `schematic.yaml`                        | Shared [Image Factory](https://factory.talos.dev) schematic               |
| `talsecret.sops.yaml`                   | SOPS-encrypted secrets bundle (native `talosctl` format)                  |
| `mod.just`                              | Recipes (`just talos ...`)                                                |

## Rendering

`just talos render-config <node>` builds the final machine config in three layers. Conceptually,
where `<role>` is `controlplane` (the only role today):

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
`vip` is defined once in `mod.just` and passed to every layer. `gateway` and `log_relay_ip` are
read from `OPNSENSE_IP` and `TALOS_LOG_RELAY_IP` in
`kubernetes/components/common/cluster-settings.yaml`, the values Flux uses for the router and
the relay's Service. Node addresses and the `192.168.0.0/24` subnet are still
literals in the files that use them; only these three are centralised.

Documents are laid out to keep `diff-node` honest: `talosctl` diffs a config **textually**, so moving
a document between layers reorders the output stream and reads as a change even when the content is
byte-identical. Content shared by every node lives in the layer that owns it;
documents that are identical per node but would reorder the stream stay put.

## Schematics

`just talos schematic-id <node>` POSTs the schematic to the Image Factory and returns its
content-addressed ID. `render-config` puts it in `UnattendedInstallConfig.installer.image`
(`factory.talos.dev/metal-installer/<id>:<version>`), and `download-image` uses it to fetch the ISO.

Resolution is per node: `nodes/<role>/<node>.schematic.yaml` wins when present, otherwise
`schematic.yaml` applies. Overrides are complete files, not deltas. Today only `control-1`
overrides, because it is the TrueNAS VM and the only GPU host (R9700 and RTX 2070).

Schematics are plain YAML, deliberately not templates. Routing them through `template` would
decrypt the secrets bundle to render a file that references no secrets. If a schematic ever needs a
variable, add the extension back.

**The ID is content-addressed, so any change to a schematic's fields moves it** — including a
one-character change to `extraKernelArgs`. Comments and formatting do not: the Factory canonicalises
the YAML before hashing, verified by stripping a comment and getting the same id back. A running
node only picks up a new ID through an upgrade (`just talos upgrade-node` reads the rendered image).

## Gotchas

- `machine.ca` merges as a cert+key **unit**: a layer supplying only `key` blanks `crt`. This is why
  `controlplane.yaml.j2` repeats the `crt` alongside the key.
- The 1.15 CA documents (`EtcdCAConfig`, `Kube*CAConfig`, `KubeServiceAccountConfig`) take raw PEM,
  v1alpha1 takes base64. The `template` recipe adds `pem`, a decoded copy of `certs`, for the former.
- `minijinja-cli` must run with autoescape off and strict on. `.minijinja.toml` sets both, found via
  `MINIJINJA_CONFIG_FILE` (mise sets it; the `template` recipe refuses to run without it). The default
  JSON-escapes every substitution, which silently wraps certs and versions in quotes and produces a
  config that looks right and is not.
- `talsecret.sops.yaml` is the native `talosctl` secrets bundle, not a talhelper format. `talosctl gen
  config --with-secrets` consumes it directly. Do not rename its keys; the templates and
  `just talos talosconfig` both depend on them.

## Talos 1.14 adoption

The version lives in the tuppr CR, not here.

A node rejects the whole config if it contains a document its version does not know (a 1.13.9
node answered `"CRICustomizationConfig" "v1alpha1": not registered`), so `apply-node` and
`diff-node` both fail against a node that has not been upgraded yet. Adopt a new document only
after tuppr has rolled every node, and re-run `diff-node` on all three before applying.

### Adopted

| Document | Replaces / adds |
| --- | --- |
| `CRICustomizationConfig` | the `machine.files` drop-in at `/etc/cri/conf.d/20-customization.part`. Editing it restarts CRI instead of needing a reboot |
| `FilesystemTrimConfig` | fstrim, weekly. Absent means no automatic trimming at all |
| `FilesystemScrubConfig` | `xfs_scrub`, weekly, off by default |
| `SysctlConfig` | `machine.sysctls`, which 1.14 deprecates |
| `UdevRulesConfig` | caps NVMe (and control-1's virtio `vd*`) `discard_max_bytes` at 256 MiB; the 2 TiB NVMe and 2 GiB virtio limits let weekly trim send multi-second discards |
| `EtcFileConfig` | `/etc/gvisor/runsc-hostnet.toml` for the `runsc-hostnet` handler (`cri/` is a forbidden prefix). Upstream's `nfsmount.conf` (nconnect 8, 1MiB rsize/wsize) is still unadopted; our five NFS mounts run at kernel defaults |

Both filesystem documents pick a stable hash-derived slot per volume per node, so the fleet does
not scrub or trim in lockstep. That is what makes weekly safe on control-3 despite its
thermal-shutdown history: it never runs at the same moment as its peers.

Trim reaches only what the node can discard. control-2 and control-3 install to real NVMe; control-1
is the TrueNAS VM, installs to virtio `/dev/vda` (its `nvme` devices are TrueNAS NVMe-oF
namespaces), so its discards pass through to whatever the hypervisor does with them.

### Deliberately not adopted

| Document | Why not |
| --- | --- |
| `OOMConfig` | a PSI-expression OOM handler. Real potential here given this cluster's OOM cascades, but it changes which cgroup dies under pressure. Needs a baseline first, not a blind default |
| `SecurityProfileConfig` | `workloadIsolation: true` (sandboxd) is a runtime change for every workload; wants its own change and its own rollback |
| `KubeletConfig` | **blocked, not deferred.** `machine.kubelet.extraMounts` has no equivalent (`ExtraMounts()` is still `return nil` in v1.15.0-beta.1) and we bind-mount `/var/openebs/local` through it. Mutually exclusive with `machine.kubelet`. `KubeNodeConfig` is separate and adopted (1.15 section) |
| `SysfsConfig` / `CRIBaseRuntimeSpecConfig` | the other two v1alpha1 fields 1.14 deprecates; neither is used in this repo |
| `RAIDArrayConfig`, `LVM*Config`, `BGPInstanceConfig`, `VethConfig` | new capabilities, none currently needed |

`VolumeConfig`'s `filesystem.xfs.minAllocationGroupSize` only affects volumes Talos formats, so it
is a wipe-time decision rather than a live one.

Evaluate separately rather than adopting blindly: NRI is enabled by default in 1.14,
`net.ipv4.conf.*.send_redirects` defaults to `0`, and etcd's HTTP endpoints move `2379` → `2383`
(we are unaffected, `listen-metrics-urls` is pinned to `2381`).

## Talos 1.15 adoption

Same rule as 1.14: apply only after a node runs 1.15, since a 1.14 node rejects the whole config
over `DiskSMARTConfig`.

**Rollout order.** tuppr builds a node's target from the install image in its *live* config, which
still names the self-built `ghcr.io/tanguille/installer/*` repos that no longer get new tags. So
merging first parks tuppr on an image nothing publishes. Move the nodes by hand from this branch
before merging, one at a time (control-1 last), waiting for `Ready` and a running Cilium agent:

```sh
just talos upgrade-node <node> <ip>   # reads the rendered Factory image for v1.15.0-beta.1
```

Then merge (tuppr finds every node at its target and completes), run `diff-node` on all three, and
`apply-node` one node at a time (etcd config changes now restart etcd without a reboot) to land the
Factory install image and `DiskSMARTConfig`. A 1.14 UKI cannot read that config, so `talosctl
rollback` is off the table from here. Only then drop the `talosctl.image.tag` pins.

| Document | Replaces / adds |
| --- | --- |
| `KubePrismConfig` | `machine.features.kubePrism` (mutually exclusive) |
| `DiskSMARTConfig` | Talos-native SMART collection (`SMARTStatus`); off on upgraded clusters until the document exists |

Every deprecated v1alpha1 section moved to its document; v1alpha1 keeps only fields with no
replacement (`machine.type/token/ca/certSANs/logging/features.diskQuotaSupport`, `cluster.token`) and
`machine.kubelet` (blocked, see 1.14). Each document conflicts with its legacy field, so a section
moves whole. Behaviour is unchanged except where noted:

| Documents | Replace | Note |
| --- | --- | --- |
| `ResolverConfig`, `TimeSyncConfig` | `machine.network.nameservers` + `features.hostDNS`, `machine.time` | |
| `DiscoveryServiceConfig`, `DiscoveryIdentityConfig` | `cluster.discovery`, `cluster.id/secret` | |
| `KubeNodeConfig` | `kubelet.nodeIP`, `nodeLabels`, `allowSchedulingOnControlPlanes` | control-plane label set explicitly, no taints (the legacy shim added both) |
| `KubeTalosAPIAccessConfig` | `features.kubernetesTalosAPIAccess` | |
| `UnattendedInstallConfig` | `machine.install` | `wipe` defaults to true, so it is set; tuppr 0.5.14 reads `installer.image` |
| `EtcdCAConfig`, `EtcdMemberConfig` | `cluster.etcd` | |
| `KubeClusterConfig`, `KubeNetworkConfig`, `KubeServiceAccountConfig`, `KubeAPIServerCAConfig`, `KubeAggregatorCAConfig` | `clusterName`, `controlPlane`, `network`, `serviceAccount`, `ca`, `aggregatorCA` | |
| `KubeEtcdEncryptionConfig` | `secretboxEncryptionSecret` | keeps key name `key2` and the `identity` provider |
| `KubeAPIServerConfig` + `KubeAuthenticationConfig` + 2x `KubeAuthorizerConfig` | `cluster.apiServer` | anonymous now allowed on `/livez`, `/readyz`, `/healthz` (was `--anonymous-auth=false`); authorizers explicit |
| `KubeControllerManagerConfig`, `KubeSchedulerConfig` | `controllerManager`, `scheduler` | |
| `KubeProxyConfig`, `KubeCoreDNSConfig` (`enabled: false`) | `proxy`, `coreDNS` | missing means enabled |

Not adopted: `KubeAdmissionControlConfig` (gen config's PodSecurity `baseline` would start enforcing
cluster-wide) and `KubeAuditPolicyConfig` (the fallback is already the default Metadata policy).

The kernel is stock (the self-built pipeline is in git at `a9ed961f5f`); the schematics set
`preempt=full` over its `PREEMPT_LAZY`. containerd config moves to version 4; our
`CRICustomizationConfig` documents only use `plugins."io.containerd.cri.v1.*"`, which did not move.

## Common tasks

These use the repo's pinned `talosctl` and `minijinja-cli`. `.envrc` puts them on `PATH` via mise,
so the bare commands below are the pinned ones. Without direnv, add the shims
(`export PATH="$HOME/.local/share/mise/shims:$PATH"`) or prefix with `mise exec --`; a `talosctl`
picked up from the system `PATH` is a different version than this pipeline is tested against.

```sh
just talos render-config <node>          # render a node's full machine config to stdout
just talos diff-node <node> <ip>         # dry-run the rendered config against the running node
just talos apply-node <node> <ip>        # render and apply
just talos upgrade-node <node> <ip>      # upgrade Talos to the node's rendered install image
just talos upgrade-k8s                   # upgrade Kubernetes to the version in the tuppr CR
just talos talosconfig                   # regenerate the client config from the secrets bundle
just talos download-image <node> <ver>   # fetch a metal ISO from the Image Factory
```

Verify any refactor of these templates by running `diff-node` against **every** node and confirming
each reports `No changes.` before applying anything. A section moving to a document is the exception:
the text diff is never empty, so compare the derived resources (`apiserverconfigs`,
`authorizationconfigs`, `schedulerconfigs`, node labels) before and after instead.
