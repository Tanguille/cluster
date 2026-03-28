# Learned User Preferences

**When to use:** revert, undo, resources, memory, CPU, MCP vs shell, Flux reconcile, ToolHive.

Maintained from session feedback. Prefer git revert, don't undo user changes, only adjust resources where already set.

- **Prefer MCP tools over raw bash** when an MCP tool is available for the task; use shell only when no tool fits or for one-off local ops.
- Prefer git revert over doing two separate commits to undo something.
- If the codebase changed between the user's last query and now, treat those changes as intentional and do not undo them.
- When adjusting cluster resources (e.g. memory overcommit), only modify workloads that already have an explicit resources block; do not add new resources blocks to workloads that rely on chart defaults.
- When adding or fixing an app, match how other similar apps in the repo handle config and secrets (e.g. cluster-secrets); do not introduce a different pattern (e.g. moving the app's Kustomization to another namespace) unless that is the established pattern.
- For Flux reconciles and cluster diagnostics, use ToolHive flux-operator MCP tools (e.g. reconcile_flux_kustomization, get_kubernetes_logs) when available instead of raw kubectl.
- Avoid running containers as root when not necessary.
- For Helm values and Kustomize in this repo, use DRY YAML anchors and the same patterns as peer apps unless there is a reason not to (`AGENTS.md` code style).
- Prefer fixing root causes (disk space, capacity, config) over silencing or weakening alerts—for Ceph mon disk warnings, add EPHEMERAL headroom on the node (prune, expand VM disk / Talos layout) instead of only lowering `mon_data_avail_warn`.
