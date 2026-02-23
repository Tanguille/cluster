# Learned User Preferences

**When to use:** revert, undo, resources, memory, CPU, MCP vs shell.

Maintained from session feedback. Prefer git revert, don't undo user changes, only adjust resources where already set.

- **Prefer MCP tools over raw bash** when an MCP tool is available for the task; use shell only when no tool fits or for one-off local ops.
- Prefer git revert over doing two separate commits to undo something.
- If the codebase changed between the user's last query and now, treat those changes as intentional and do not undo them.
- When adjusting cluster resources (e.g. memory overcommit), only modify workloads that already have an explicit resources block; do not add new resources blocks to workloads that rely on chart defaults.
