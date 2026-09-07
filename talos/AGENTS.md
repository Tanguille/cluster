# Talos Guidance

- Read [the README](README.md) for the layer model and the recipe list before changing machine
  configuration, and the Talos entries in [learned workspace facts](../.agents/learned-workspace.md).
- **Always** run `just talos diff` and read the per-node output before applying anything. A config
  that merely renders and validates is not evidence; the dry-run diff against the running node is.
- Ask before applying configuration or upgrading a live node.
- Machine configs are **never** applied automatically. Nothing in Flux, CI or tuppr pushes them;
  merging a change to `talos/` only changes what `just talos render` produces.
- A node rejects the whole config if it contains a document its version does not know, so during
  any mixed-version window a new document in `all/` breaks `diff` fleet-wide. `topf` reads each
  node's *running* version to pick the config contract, so a mixed fleet renders differently
  per node.
- A new node must be added to `topf.yaml` **and** have its installer built
  (`just talos kernel-build <host>`). There is no usable cluster-layer fallback: topf's generated
  installer points at the Image Factory, which would install a stock kernel.
- Never run `topf secrets` in `talos/` — it writes to `secretsPath`.
- Do not add a recipe wrapping `topf upgrade`; it is broken against this fleet's installer refs
  (see the README gotchas). tuppr performs upgrades.
- `control-1` is the TrueNAS VM and the only dGPU host. Upgrade it last, and never taint it or
  GPU workloads have nowhere to schedule.
