---
name: pr-review
description: |-
  Run 6-phase parallel PR reviews for GitOps Kubernetes. Spawns subagents for YAML format,
  naming, best practices, security, architecture, and validation. Delegates focused tasks to
  prevent context pollution. Produces severity-graded findings with actionable summaries.
  Isolates each review by PR number to prevent collisions. Also reviews local git diff
  before commit (local-changes identifier).

  Use proactively for: PRs adding/modifying K8s apps, Flux resources, HelmReleases,
  infrastructure changes, pre-CI validation needs, or local git diff review before commit.

  Examples:
  - user: "Review this PR" → spawn 6 subagents in parallel, aggregate findings
  - user: "Check my app config" → delegate to best-practices subagent
  - user: "Are secrets encrypted?" → delegate to security subagent
  - user: "Validate before CI" → spawn local validation subagent
  - user: "Check phase 3 only" → single subagent for specific phase
  - user: "Review my local changes" → review git diff with local-changes identifier
  - user: "Check what I'm about to commit" → review staged/unstaged changes
---

# PR Review Skill

Spawn specialized subagents to review GitOps Kubernetes PRs. Each phase runs independently with clean context to maximize thoroughness and speed.

## When to Delegate to Subagents

**PARALLEL (spawn 3+ at once):**

- Multiple independent review dimensions
- No shared state between checks
- Different file patterns (YAML vs scripts)

**SEQUENTIAL (one after another):**

- Later phase depends on earlier results
- Shared file analysis
- Validation requires build output

**INLINE (do yourself):**

- Single file, <20 lines
- Simple checklist verification
- Aggregate results only

## Repository Context

- **Structure**: `kubernetes/apps/<namespace>/<app>/` with `ks.yaml` + `app/`
- **Charts**: `bjw-s/app-template` (standard), other Helm charts in `charts/`
- **Ingress**: Envoy Gateway with parentRef `envoy-internal` or `envoy-external`
- **URLs**: `${SECRET_DOMAIN}` template (never hardcode)
- **Secrets**: SOPS encryption (`.sops.yaml` config), never plaintext
- **Naming**: lowercase-dashes for resources, kebab-case files
- **Format**: YAML 2-space indent, LF, `yamllint`
- **Validation**: `kubeconform -strict`, `kustomize build`, `flux build`

## Quick Start

```
Review this PR
# Spawns 6 subagents in parallel, aggregates results

Review PR phases 1,3,6
# Spawn specific phases

Check if secrets are encrypted
# Delegate security check to subagent

Review my local changes
# Review git diff (staged + unstaged) with local-changes identifier

What am I about to commit?
# Review staged changes only
```

## Usage

### 1. Initialize with PR Identifier

**EXTRACT PR NUMBER** from URL or input:

- URL: `https://github.com/Tanguille/cluster/pull/2223/` → `PR_ID=2223`
- Input: "Review PR #1234" → `PR_ID=1234`
- Branch: `feat/my-feature` → `PR_ID=my-feature`

```bash
# Create isolated directory for this PR
PR_ID="2223"  # Extract from URL or user input
mkdir -p .opencode/pr-review/pr-${PR_ID}

# Check for existing review
ls -la .opencode/pr-review/pr-${PR_ID}/ 2>/dev/null || echo "Fresh start"
```

**HARNESS PRINCIPLE**: Each PR gets isolated directory to prevent collisions when reviewing multiple PRs concurrently. Phase outputs live under `.opencode/pr-review/pr-${PR_ID}/phase-*.md`. Any root-level phase files (e.g. `pr-review-phase-1-yaml.md`) are legacy; archive or delete when consolidating.

Ask user if files exist:

- **Continue**: Only missing phases
- **Fresh start**: Delete `.opencode/pr-review/pr-${PR_ID}/` and rerun

### 1b. Initialize for Local Git Diff Review

**When user asks to review local changes:**

```bash
# Use special identifier for local changes
PR_ID="local-changes"
mkdir -p .opencode/pr-review/pr-${PR_ID}

# Get the diff
git diff --cached --name-only > .opencode/pr-review/pr-${PR_ID}/staged-files.txt
git diff --cached > .opencode/pr-review/pr-${PR_ID}/staged.diff
git diff --name-only > .opencode/pr-review/pr-${PR_ID}/unstaged-files.txt
git diff > .opencode/pr-review/pr-${PR_ID}/unstaged.diff
```

**Output files:**

- `staged-files.txt` - List of staged files
- `staged.diff` - Staged changes diff
- `unstaged-files.txt` - List of unstaged files
- `unstaged.diff` - Unstaged changes diff

Use these files as input to the same 6-phase review process.

### 2. Launch Subagents (PARALLEL)

Spawn ALL 6 subagents in ONE message with `subagent_type: "general"`:

```
Task: pr-review-phase-1
Task: pr-review-phase-2
Task: pr-review-phase-3
Task: pr-review-phase-4
Task: pr-review-phase-5
Task: pr-review-phase-6
```

Each subagent gets clean context - no need to pass full SKILL.md. Just phase-specific instructions.

#### Subagent: Phase 1 (YAML Format)

**Spawn with:**

```yaml
subagent_type: general
description: PR Review Phase 1: YAML Format
prompt: |
  Review YAML formatting for PR.

  CONVENTIONS:

  - 2-space indentation (no tabs)
  - LF line endings
  - No trailing whitespace
  - Blank line at EOF

  FILES: [list from PR]

  TASKS:

  1. Check 2-space indentation on all YAML
  2. Verify no tabs
  3. Check trailing whitespace
  4. Run: yamllint -c .yamllint.yaml kubernetes/

  OUTPUT to .opencode/pr-review/pr-${PR_ID}/phase-1-yaml-format.md:

  # Phase 1: YAML Format Review

  **Completed:** [timestamp]

  ## Files Reviewed

  (list)

  ## Findings

  | Severity | File | Line | Issue | Fix |
  |----------|------|------|-------|-----|

  ## Summary Stats

  - Total issues: N
  - Critical: N | High: N | Medium: N | Low: N
```

#### Subagent: Phase 2 (Naming)

**Spawn with:**

```yaml
subagent_type: general
description: PR Review Phase 2: Naming Conventions
prompt: |
  Review naming conventions for PR.

  CONVENTIONS:

  - Resources: lowercase-dashes
  - Files: kebab-case.yaml
  - Directories: kubernetes/apps/<namespace>/<app>/

  TASKS:

  1. Check resource names (lowercase-dashes)
  2. Verify file naming
  3. Check directory structure
  4. Verify ks.yaml name matches dir

  OUTPUT to .opencode/pr-review/pr-${PR_ID}/phase-2-naming.md with findings table.
```

#### Subagent: Phase 3 (Best Practices)

**Spawn with:**

```yaml
subagent_type: general
description: PR Review Phase 3: Best Practices
prompt: |
  Review HelmRelease best practices for PR.

  STANDARDS:

  - Chart: bjw-s/app-template with pinned version
  - Annotations: reloader.stakater.com/auto: "true"
  - Probes: livenessProbe + readinessProbe
  - Resources: requests + limits
  - Security: securityContext
  - Routes: envoy-internal parentRef
  - Hostnames: {{ .Release.Name }}.${SECRET_DOMAIN}

  TASKS:

  1. Check chart source/version
  2. Verify reloader annotation
  3. Check probes defined
  4. Verify resources
  5. Check securityContext
  6. Verify persistence
  7. Check HTTPRoute parentRef
  8. Verify hostname template

  OUTPUT to .opencode/pr-review/pr-${PR_ID}/phase-3-best-practices.md.
```

#### Subagent: Phase 4 (Security)

**Spawn with:**

```yaml
subagent_type: general
description: PR Review Phase 4: Security
prompt: |
  Review security for GitOps Kubernetes PR.

  REQUIREMENTS:

  - Secrets MUST be SOPS encrypted (sops: key present)
  - No hardcoded credentials
  - No hardcoded domains (use ${SECRET_DOMAIN})
  - securityContext non-root

  TASKS:

  1. Find all Secret resources
  2. Verify SOPS encryption
  3. Check for hardcoded credentials
  4. Check for hardcoded domains/IPs
  5. Verify securityContext

  OUTPUT to .opencode/pr-review/pr-${PR_ID}/phase-4-security.md.
```

#### Subagent: Phase 5 (Architecture)

**Spawn with:**

```yaml
subagent_type: general
description: PR Review Phase 5: Architecture
prompt: |
  Review architecture patterns for GitOps Kubernetes PR.

  STANDARDS:

  - Structure: ks.yaml + app/ subdirectory
  - ks.yaml → app/kustomization.yaml
  - DRY: YAML anchors for repeated values

  TASKS:

  1. Verify ks.yaml exists
  2. Check app/ subdirectory
  3. Verify kustomization.yaml references
  4. Check YAML anchors for DRY

  OUTPUT to .opencode/pr-review/pr-${PR_ID}/phase-5-architecture.md.
```

#### Subagent: Phase 6 (Validation)

**Spawn with:**

```yaml
subagent_type: general
description: PR Review Phase 6: Validation
prompt: |
  Run validation tools for GitOps Kubernetes PR.

  TOOLS:

  - yamllint: YAML syntax
  - kubeconform: K8s schemas
  - kustomize build: Kustomization validity
  - flux build: Flux reconciliation

  COMMANDS:

  - yamllint -c .yamllint.yaml kubernetes/
  - kubeconform -strict kubernetes/
  - kustomize build kubernetes/apps/<namespace>/<app>/
  - flux build kustomization <name> --path <path>

  TASKS:

  1. Run yamllint on changed YAML
  2. Run kubeconform
  3. Run kustomize build
  4. Run flux build

  OUTPUT to .opencode/pr-review/pr-${PR_ID}/phase-6-validation.md.
```

### 3. Aggregate Results (INLINE - Do yourself)

After all subagents complete, aggregate:

```bash
PR_ID="2223"  # Use same PR_ID from initialization (or "local-changes" for git diff)
ls .opencode/pr-review/pr-${PR_ID}/phase-*.md
```

**For PR reviews:** Include PR URL, branch info
**For local changes:** Include `git diff --stat` summary

Create `.opencode/pr-review/pr-${PR_ID}/pr-review-state.md`:

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

1. [critical/high]
2. ...

### Quick Fixes

- [easy wins]

### Per-Phase Summaries

(Brief bullets)

## Detailed Reports

- .opencode/pr-review/pr-${PR_ID}/phase-1-yaml-format.md
- .opencode/pr-review/pr-${PR_ID}/phase-2-naming.md
- .opencode/pr-review/pr-${PR_ID}/phase-3-best-practices.md
- .opencode/pr-review/pr-${PR_ID}/phase-4-security.md
- .opencode/pr-review/pr-${PR_ID}/phase-5-architecture.md
- .opencode/pr-review/pr-${PR_ID}/phase-6-validation.md
```

### 4. Present Results (INLINE)

```
## PR Review Complete

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 2 |
| Medium | 3 |
| Low | 5 |

### 🔴 Blocking

[Critical issues]

### ⚠️ Should Fix

[High priority]

### ✅ Quick Wins

[Low effort fixes]

See .opencode/pr-review/pr-${PR_ID}/ for details
```

## Quick Checklist (INLINE Tasks)

For small reviews, check inline without subagents:

- [ ] **Format**: 2-space indent, LF, no trailing whitespace
- [ ] **Naming**: lowercase-dashes, matching directories
- [ ] **Structure**: ks.yaml + app/ pattern
- [ ] **Chart**: bjw-s/app-template with pinned version
- [ ] **Reloader**: reloader.stakater.com/auto: "true"
- [ ] **Probes**: liveness + readiness defined
- [ ] **Resources**: requests + limits set
- [ ] **Security**: securityContext, SOPS encrypted
- [ ] **Routes**: envoy-internal parentRef
- [ ] **Domains**: ${SECRET_DOMAIN} template
- [ ] **Validation**: yamllint, kubeconform pass

## Subagent Delegation Patterns

### When to Spawn Subagents

| Scenario | Pattern | Why |
|----------|---------|-----|
| Full PR review | 6 parallel subagents | Independent checks, max speed |
| Single phase only | 1 subagent | Clean context for focus |
| Large PR (20+ files) | Parallel by file group | Prevent context overflow |
| Validation after fixes | Sequential | Depends on changes |
| Security audit | Dedicated subagent | Specialized focus |

### Subagent Benefits

1. **Clean Context**: Each gets fresh context window
2. **Parallel Speed**: 6x faster than sequential
3. **Focused**: Single responsibility per agent
4. **Isolation**: Errors in one don't pollute others
5. **Scalable**: Can add more phases without bloat

### Anti-Patterns

❌ **Don't**: Spawn subagent for single file, 5-line change
❌ **Don't**: Pass entire SKILL.md to every subagent
❌ **Don't**: Spawn sequentially when parallel works
❌ **Don't**: Aggregate before all subagents complete
❌ **Don't**: Review local changes without checking both staged and unstaged

✅ **Do**: Spawn for independent tasks
✅ **Do**: Give subagents minimal context needed
✅ **Do**: Launch all in one message
✅ **Do**: Let subagents write directly to disk
✅ **Do**: Use `local-changes` identifier for git diff reviews
✅ **Do**: Show staged vs unstaged summary in output

## Harness Engineering Principles

### Isolation by PR Identifier

**NEVER** use shared output paths. Each review gets isolated directory:

```
.opencode/pr-review/
├── pr-2223/                    # PR #2223 review
│   ├── phase-1-yaml-format.md
│   ├── phase-2-naming.md
│   ├── pr-review-state.md
│   └── ...
├── pr-1234/                    # PR #1234 review (concurrent)
│   ├── phase-1-yaml-format.md
│   └── ...
└── pr-feature-x/               # Branch-based review
    └── ...
```

**Why**: Prevents collisions when:

- Reviewing multiple PRs concurrently
- Re-running review after fixes
- Comparing reviews over time
- Multiple agents reviewing different PRs

### Concurrent Review Support

```bash
# Review PR #2223
PR_ID=2223
mkdir -p .opencode/pr-review/pr-${PR_ID}
# ... spawn subagents ...

# Simultaneously review PR #1234
PR_ID=1234
mkdir -p .opencode/pr-review/pr-${PR_ID}
# ... spawn subagents ...

# Both run independently, no conflicts
```

### Cleanup Policy

Reviews are preserved for reference:

- Keep: Last 10 reviews per repo
- Archive: Older reviews to `.opencode/pr-review/archive/`
- User can: `rm -rf .opencode/pr-review/pr-${PR_ID}/` when done

## References

- `references/best-practices.md` - Current best practices
- `scripts/validate-pr.sh` - Local validation script
