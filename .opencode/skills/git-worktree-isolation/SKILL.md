---
name: git-worktree-isolation
description: |-
  Use git worktrees for isolated, parallel work. Creates temporary worktrees for
  independent agent tasks without affecting the main working directory.

  Use proactively when:
  - user: "work on feature X" → create worktree, work in isolation
  - user: "experiment with Y" → worktree to avoid main branch pollution
  - user: "parallel task" → separate worktree for concurrent work

  Triggers: worktree, isolated work, parallel agent, experimental branch, feature branch
---

# Git Worktree Isolation

## Create Worktree

First check whether you are already in a task-specific worktree. If yes, do not create another one.

Check status before creating a worktree:

```bash
git fetch origin
git worktree list
git status --short
```

Create a branch from the chosen base branch:

```bash
git worktree add -b <new-branch> work/isolated-<task>-<timestamp> origin/<base-branch>
```

Use the Bash tool `workdir` parameter for subsequent commands. Do not use `cd` in bash commands.

## Validate

Validate Kubernetes manifests:

```bash
mise exec -- kubeconform -strict kubernetes/
```

Test Flux reconciliation:

```bash
mise exec -- flux-local test --all-namespaces --enable-helm kubernetes/flux/cluster
```

## Cleanup

Before cleanup, ask @explorer to inspect git worktree status and report whether `work/isolated-<task>-<timestamp>` and branch `<branch>` are safe to remove. The subagent must not delete anything.

Or manual cleanup from the main repository workdir:

Remove the worktree:

```bash
git worktree remove work/isolated-<task>-<timestamp>
```

Delete the branch:

```bash
git branch -D <branch>
```

## When to Delegate

| Task | Delegate? | Notes |

|------|-----------|-------|

| Simple worktree creation | No | Inline in main agent |

| Validation/tests | Yes | Can run parallel via subagent |

| Cleanup (remove worktree/branch) | Optional | Ask @explorer to inspect safety first; perform deletion yourself after review |

| Multiple worktrees | Yes | Parallel creation via subagent |

## Permission

Add "git-worktree-isolation" to `opencode.json`'s `"allow"` section to grant all agents automatic access.
