# PR review phase subagent prompts

Spawn each phase with `subagent_type: general` (or `code-reviewer` only when the user requests a single focused pass). Replace `PR_ID` and file lists from the PR or local diff.

Output paths: `.agents/pr-review/pr-${PR_ID}/phase-<N>-*.md`

## Phase 1 — YAML format

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

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-1-yaml-format.md:

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

## Phase 2 — Naming

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

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-2-naming.md with findings table.
```

## Phase 3 — Best practices

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

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-3-best-practices.md.
```

See [best-practices.md](best-practices.md) for expanded validation topics.

## Phase 4 — Security

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

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-4-security.md.
```

## Phase 5 — Architecture

```yaml
subagent_type: general
description: PR Review Phase 5: Architecture
prompt: |
  Review architecture patterns for GitOps Kubernetes PR.

  STANDARDS:
  - Structure: ks.yaml + app/ subdirectory
  - ks.yaml → app/kustomization.yaml
  - DRY: YAML anchors for repeated values (single document only)

  TASKS:
  1. Verify ks.yaml exists
  2. Check app/ subdirectory
  3. Verify kustomization.yaml references
  4. Check YAML anchors for DRY

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-5-architecture.md.
```

## Phase 6 — Validation

```yaml
subagent_type: general
description: PR Review Phase 6: Validation
prompt: |
  Run validation tools for GitOps Kubernetes PR.

  TOOLS:
  - yamllint: YAML syntax
  - kustomize build: Kustomization validity
  - flux build: Flux reconciliation

  COMMANDS:
  - yamllint -c .yamllint.yaml kubernetes/
  - mise exec -- shellcheck scripts/*.sh
  - kustomize build kubernetes/apps/<namespace>/<app>/
  - flux build kustomization <name> --path <path>

  TASKS:
  1. Run yamllint on changed YAML
  2. Run shellcheck on touched scripts
  3. Run kustomize build
  4. Run flux build

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-6-validation.md.
```

Optional helper: `bash .agents/skills/pr-review/scripts/validate-pr.sh` when present and applicable.
