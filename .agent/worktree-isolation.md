# Git worktree isolation

**When to use:** worktree, isolated work, parallel agent, experimental branch, feature branch.

Always to avoid conflicts with other agents and the main working directory.

## Create worktree

```bash
git fetch origin
git checkout <branch>
git pull origin <branch>

git worktree list

# Create a detached worktree (replace <branch> and timestamp)
git worktree add -b <branch> --detach work/isolated-<task>-<timestamp> <branch>

cd work/isolated-<task>-<timestamp>
```

## Validate (from worktree root)

```bash
yamlfmt -w kubernetes/
kubeconform -strict -original-location kubernetes/
flux-local test --all-namespaces --enable-helm --path kubernetes/flux/cluster
```

## Cleanup

```bash
cd /path/to/main/repo
git worktree remove work/isolated-<task>-<timestamp>
git branch -D <branch>
```
