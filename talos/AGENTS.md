# Talos Guidance

- Read [the README](README.md) for the layer model and the recipe list before changing machine
  configuration, and the Talos entries in [learned workspace facts](../.agents/learned-workspace.md).
- **Always** run `just talos diff-node <node> <ip>` against every node and confirm each reports
  `No changes.` before applying anything. A config that merely validates is not evidence; the
  dry-run diff against the running node is.
- Ask before applying configuration or upgrading a live node.
- Machine configs are **never** applied automatically. Nothing in Flux, CI or tuppr pushes them;
  merging a change to `talos/` only changes what `render-config` produces.
- A node rejects the whole config if it contains a document its version does not know, so during
  any mixed-version window a new document in `cluster.yaml.j2` breaks `diff-node` fleet-wide.
- A new node must declare its own `machine.install.image`. There is no cluster-layer fallback:
  every node pins a custom-kernel installer, so an unpinned node renders with no image rather than
  falling back to a stock-kernel Factory build.
- `control-1` is the TrueNAS VM and the only dGPU host. Upgrade it last, and never taint it or
  GPU workloads have nowhere to schedule.
