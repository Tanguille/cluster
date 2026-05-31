---
name: git-worktree-isolation
description: >-
  Use git worktrees for isolated, parallel agent work without polluting the main working tree.

  user: "work on feature X" → create worktree and branch under work/isolated-*
  user: "experiment with Y" → detached worktree for safe trials
  user: "parallel task" → separate worktree per concurrent task

  Triggers: worktree, isolated work, parallel agent, experimental branch, feature branch.
compatibility: Requires `git` 2.x worktree support and write access to the repository; validation uses `mise` and `flux-local` when run from the worktree.
---

# Git worktree isolation

## Create worktree

```bash
git fetch origin
git checkout <branch>
git pull origin <branch>
git worktree list
git worktree add -b <branch> --detach work/isolated-<task>-<timestamp> <branch>
cd work/isolated-<task>-<timestamp>
```

## Validate (in worktree)

```bash
mise exec -- shellcheck scripts/*.sh
bash .agents/skills/pr-review/scripts/validate-pr.sh
flux-local test --all-namespaces --enable-helm kubernetes/flux/cluster
```

For manifest-only changes, yamllint and `kustomize build` on touched paths under `kubernetes/apps/` are enough before the full script.

## Cleanup

From the **main repo** (not inside the worktree path):

```bash
git worktree remove work/isolated-<task>-<timestamp>
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

Format reference: [agentskills.io](https://agentskills.io/specification).
