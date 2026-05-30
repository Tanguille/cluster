# PR review workflow and isolation

## Harness: isolated output per review

**Never** use shared output paths. Each review gets its own directory:

```text
.agents/pr-review/
├── pr-2223/
│   ├── phase-1-yaml-format.md
│   ├── phase-2-naming.md
│   ├── pr-review-state.md
│   └── ...
├── pr-local-changes/
│   └── ...
```

**Why:** concurrent PR reviews, re-runs after fixes, and multiple agents do not collide.

Legacy root-level `pr-review-phase-*.md` files are obsolete; consolidate under `pr-${PR_ID}/`.

## Local diff initialization

```bash
PR_ID="local-changes"
mkdir -p .agents/pr-review/pr-${PR_ID}
git diff --cached --name-only > .agents/pr-review/pr-${PR_ID}/staged-files.txt
git diff --cached > .agents/pr-review/pr-${PR_ID}/staged.diff
git diff --name-only > .agents/pr-review/pr-${PR_ID}/unstaged-files.txt
git diff > .agents/pr-review/pr-${PR_ID}/unstaged.diff
```

Run the same six phases against these artifacts. Summarize staged vs unstaged in the final report.

## Aggregation template (`pr-review-state.md`)

```markdown
# PR Review Session

**Started:** [timestamp]
**Completed:** [timestamp]
**Status:** Complete

## Progress
- [x] Phase 1: YAML Format
- [x] Phase 2: Naming Conventions
- [x] Phase 3: Best Practices
- [x] Phase 4: Security
- [x] Phase 5: Architecture
- [x] Phase 6: Validation

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
- .agents/pr-review/pr-${PR_ID}/phase-1-yaml-format.md
- ...
```

## Delegation patterns

| Scenario | Pattern | Why |
|----------|---------|-----|
| Full PR review | 6 parallel subagents | Independent checks |
| Single phase | 1 subagent | Focused context |
| Large PR (20+ files) | Parallel by file group | Avoid context overflow |
| Validation after fixes | Sequential | Depends on changes |
| Security audit | Dedicated subagent | Specialized focus |

**Do:** launch independent phases in one message; minimal prompts; write outputs to disk.

**Don't:** spawn subagents for trivial one-file edits; pass full SKILL.md to every subagent; aggregate before all phases finish; skip unstaged files on local reviews.

## Cleanup

- Keep recent reviews for reference; user may `rm -rf .agents/pr-review/pr-${PR_ID}/` when done.
- Optional archive: `.agents/pr-review/archive/`
