---
name: git-worktree-isolation
description: >-
  Use git worktrees for isolated, parallel agent work without polluting the main working tree.

  user: "work on feature X" → create worktree and branch under .worktrees/<task>
  user: "experiment with Y" → detached worktree for safe trials
  user: "parallel task" → separate worktree per concurrent task

  Triggers: worktree, isolated work, parallel agent, experimental branch, feature branch.
compatibility: Requires `git` 2.x worktree support and write access to the repository; validation uses `mise` when run from the worktree.
---

# Git worktree isolation

## Create worktree

```bash
# new branch
git fetch origin
git worktree add -b <branch> .worktrees/<task> origin/main
cd .worktrees/<task>
cp -r ../../.env ../../.mcp.json ../../.vscode . 2>/dev/null  # untracked local config; add your agent-tool config dir too

# detached experiment
git worktree add --detach .worktrees/<task> <commit-ish>
```

## Validate (in worktree)

```bash
bash .agents/skills/pr-review/scripts/validate-pr.sh
```

For manifest-only changes, `mise exec -- flate test all` (renders the HelmRelease; falls back to `kustomize build` on touched paths if `flate` is unavailable) is enough before the full script.

## Cleanup

From the **main repo** (not inside the worktree path):

```bash
git worktree remove .worktrees/<task>
git branch -D <branch>
```

Delegate cleanup to a subagent when it should not block the main flow.

## Delegation

| Task | Delegate? |
|------|-----------|
| Simple worktree creation | No — inline |
| Validation / tests | Optional subagent |
| Cleanup | Yes — optional subagent |
| Multiple worktrees | Yes — parallel subagents |
