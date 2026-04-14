# Learned User Preferences

**When to use:** revert, undo, resources, memory, CPU, MCP vs shell, Flux reconcile, ToolHive, find_tool, call_tool, tool confidence, proactive tools.

Maintained from session feedback. Prefer git revert, don't undo user changes, only adjust resources where already set.

## Tool usage and confidence

When external tools are available, use them proactively. Do not answer from memory if a tool can provide accurate, current data.

### ToolHive (`find_tool` / `call_tool`)

If the available tools include `find_tool` and `call_tool` (ToolHive unified gateway), always follow this sequence:

1. Call `find_tool` first with a relevant search query to discover what is available.
2. Examine the result to identify the correct tool name and its required parameters.
3. Call `call_tool` to execute it.
4. Interpret the result and respond naturally—never return raw JSON or raw tool output to the user.

### Other tools and behavior

- For any other tools, call them directly when relevant, without unnecessary preamble.
- When a user asks to perform an action (for example, "turn off the lights"), use the relevant tool immediately without hesitation.
- Only report uncertainty if a tool returns an error, an ambiguous response, or nothing at all.
- If no tool is available for a task after checking, say so clearly: *"I don't have access to [service]"*—do not speculate or fabricate an execution path.
- When a tool returns a successful response, report the outcome as fact.
- If a user confirms an action worked, trust that feedback. Do not second-guess it.

---

- **Prefer MCP tools over raw bash** when an MCP tool is available for the task; use shell only when no tool fits or for one-off local ops.
- Prefer git revert over doing two separate commits to undo something.
- If the codebase changed between the user's last query and now, treat those changes as intentional and do not undo them.
- When adjusting cluster resources (e.g. memory overcommit), only modify workloads that already have an explicit resources block; do not add new resources blocks to workloads that rely on chart defaults.
- When adding or fixing an app, match how other similar apps in the repo handle config and secrets (e.g. cluster-secrets); do not introduce a different pattern (e.g. moving the app's Kustomization to another namespace) unless that is the established pattern.
- For Flux reconciles and cluster diagnostics, use ToolHive flux-operator MCP tools (e.g. reconcile_flux_kustomization, get_kubernetes_logs) when available instead of raw kubectl.
- Avoid running containers as root when not necessary.
- For Helm values and Kustomize in this repo, use DRY YAML anchors and the same patterns as peer apps unless there is a reason not to (`AGENTS.md` code style).
- Prefer fixing root causes (disk space, capacity, config) over silencing or weakening alerts—for Ceph mon disk warnings, add EPHEMERAL headroom on the node (prune, expand VM disk / Talos layout) instead of only lowering `mon_data_avail_warn`.
