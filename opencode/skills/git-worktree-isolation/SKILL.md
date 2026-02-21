# Skill: git-worktree-isolation

## Description
Use git worktrees for isolated, parallel work. Creates temporary worktrees for independent agent tasks without affecting the main working directory.

## When to use
- Parallel agent work
- Experimental changes
- Testing pipelines

## How to create a worktree
```bash
# Create a detached worktree and checkout the target branch
git worktree add -b <branch> --detach work/isolated-<task>-<timestamp> <branch>
```

## Validation workflow
```bash
yamlfmt -w .
kubeconform -strict .
flux-local test --all-namespaces --enable-helm .
```

## Cleanup
```bash
git worktree remove work/isolated-<task>-<timestamp>
```

## Permission
To grant all agents access automatically, add "git-worktree-isolation" to `opencode.json`'s `"allow"` section.
