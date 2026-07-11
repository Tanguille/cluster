# Talos Context

**When to use:** Talos, schematic, kernel argument, kubelet, containerd, image garbage collection, apply node, or upgrade node.

- Talos kernel arguments such as `talos.dashboard.disabled=1` belong in `schematic.yaml`. Existing nodes require a schematic rebuild and Talos upgrade to receive them.
- Large `/var/lib/containerd` usage on EPHEMERAL is often old image layers. Configure `machine.kubelet.extraConfig.imageGCHighThresholdPercent`, `imageGCLowThresholdPercent`, and `imageMinimumGCAge` so garbage collection starts before the defaults would trigger.
- Kubelet-only machine configuration usually applies without a full reboot; confirm the Talos apply mode when a patch contains other fields.
- Generate configuration: `mise exec -- task talos:generate-config`.
- Apply to a node: `mise exec -- task talos:apply-node IP=...`.
- Upgrade a node: `mise exec -- task talos:upgrade-node IP=...`.
- Reconcile Flux: `mise exec -- task reconcile`.
- Applying configuration, upgrading nodes, and reconciling the live cluster require user approval.
