# Talos Guidance

- Read [the README](README.md) for the layer model before changing machine configuration, and the
  Talos entries in [learned workspace facts](../.agents/learned-workspace.md).
- Render with `mise exec -- just talos render-config <node>`.
- **Always** verify with `mise exec -- just talos diff-node <node> <ip>` on every node and confirm
  each reports `No changes.` before applying anything. A rendered config that merely validates is not
  evidence; the dry-run diff against the running node is.
- Apply with `mise exec -- just talos apply-node <node> <ip>`, upgrade with
  `mise exec -- just talos upgrade-node <node> <ip>`.
- Ask before applying configuration or upgrading a live node.
- `control-1` is the TrueNAS VM and the only dGPU host. Upgrade it last.
