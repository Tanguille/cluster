# Talos Guidance

- Read [the README](README.md) for the layer model before changing machine configuration, and the
  Talos entries in [learned workspace facts](../.agents/learned-workspace.md).
- Render with `just talos render-config <node>`.
- **Always** verify with `just talos diff-node <node> <ip>` on every node and confirm
  each reports `No changes.` before applying anything. A rendered config that merely validates is not
  evidence; the dry-run diff against the running node is.
- Apply with `just talos apply-node <node> <ip>`, upgrade with
  `just talos upgrade-node <node> <ip>`.
- Ask before applying configuration or upgrading a live node.
- Machine configs are **never** applied automatically. Nothing in Flux, CI or tuppr pushes them;
  merging a change to `talos/` only changes what `render-config` produces. A human runs
  `apply-node` per node.
- Talos 1.14 documents cannot be applied to a node still on 1.13: the node rejects the whole
  config, so one such document in `cluster.yaml.j2` breaks `diff-node` for every node in the fleet
  until the last one is upgraded.
- `control-1` is the TrueNAS VM and the only dGPU host. Upgrade it last.
