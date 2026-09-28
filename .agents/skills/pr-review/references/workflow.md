# PR review workflow and isolation

## Harness: isolated output per review

**Never** use shared output paths. Each review gets its own directory:

```text
.agents/pr-review/
├── pr-2223/
│   ├── phase-1-naming.md
│   ├── phase-2-best-practices.md
│   ├── pr-review-state.md
│   └── ...
├── pr-local-changes/
│   └── ...
```

**Why:** concurrent PR reviews, re-runs after fixes, and multiple agents do not collide.

## Local diff initialization

```bash
PR_ID="local-changes"
mkdir -p .agents/pr-review/pr-${PR_ID}
git diff --cached --name-only > .agents/pr-review/pr-${PR_ID}/staged-files.txt
git diff --cached > .agents/pr-review/pr-${PR_ID}/staged.diff
git diff --name-only > .agents/pr-review/pr-${PR_ID}/unstaged-files.txt
git diff > .agents/pr-review/pr-${PR_ID}/unstaged.diff
git ls-files --others --exclude-standard > .agents/pr-review/pr-${PR_ID}/untracked-files.txt
```

Run the same five phases against these artifacts. Summarize staged, unstaged, and untracked files in the final report.

## Aggregation template (`pr-review-state.md`)

```markdown
# PR Review Session

**Started:** [timestamp]
**Completed:** [timestamp]
**Status:** Complete

## Progress
- [x] Phase 1: Naming Conventions
- [x] Phase 2: Best Practices
- [x] Phase 3: Security
- [x] Phase 4: Architecture
- [x] Phase 5: Validation

## Summary
**Total Issues:** [sum]
- Critical: N (must fix)
- High: N (should fix)
- Medium: N (fix if time)
- Low: N (nice to have)

### Top 5 Priority Fixes
1. ...

### Quick Fixes
- ...

### Per-Phase Summaries
(brief bullets)

## Detailed Reports
- .agents/pr-review/pr-${PR_ID}/phase-1-naming.md
- ...
```
