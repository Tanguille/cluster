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

```bash
git fetch origin
```

```bash
git checkout <branch>
```

```bash
git pull origin <branch>
```

```bash
git worktree list
```

```bash
git worktree add -b <branch> --detach work/isolated-<task>-<timestamp> <branch>
```

```bash
cd work/isolated-<task>-<timestamp>
```

## Validate

Validate Kubernetes manifests:

```bash
kubeconform -strict kubernetes/
```

Test Flux reconciliation:

```bash
flux-local test --all-namespaces --enable-helm kubernetes/flux/cluster
```

## Cleanup

**Delegate to subagent for cleanup operations**:

```bash
background_task(agent="git-cleanup", description="Remove worktree and branch", prompt="Cleanup git worktree at work/isolated-<task>-<timestamp> and delete branch <branch>")
```

Or manual cleanup:

Navigate out of worktree first:

```bash
cd /path/to/main/repo
```

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

| Cleanup (remove worktree/branch) | Yes | Spawn subagent, don't block main flow |

| Multiple worktrees | Yes | Parallel creation via subagent |

## Permission

Add "git-worktree-isolation" to `opencode.json`'s `"allow"` section to grant all agents automatic access.
