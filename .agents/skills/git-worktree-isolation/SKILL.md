---
name: git-worktree-isolation
description: >-
  Use git worktrees for isolated, parallel agent work without polluting the main working tree.

  user: "work on feature X" → create worktree and branch under .worktrees/<task>
  user: "experiment with Y" → detached worktree for safe trials
  user: "parallel task" → separate worktree per concurrent task

  Triggers: worktree, isolated work, parallel agent, experimental branch, feature branch.
compatibility: Requires `git` 2.x worktree support and write access to the repository.
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

## Cleanup

From the **main repo** (not inside the worktree path):

```bash
git worktree remove .worktrees/<task>
git branch -D <branch>
```

Delegate cleanup to a subagent when it should not block the main flow.
