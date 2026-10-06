# PR review phase subagent prompts

Spawn each phase with `subagent_type: general-purpose` (or `code-reviewer` only when the user requests a single focused pass). Replace `PR_ID` and file lists from the PR or local diff.

Output paths: `.agents/pr-review/pr-${PR_ID}/phase-<N>-*.md`

## Phase 1 — Naming

```yaml
subagent_type: general-purpose
description: "PR Review Phase 1: Naming Conventions"
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

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-1-naming.md with findings table.
```

## Phase 2 — Best practices

```yaml
subagent_type: general-purpose
description: "PR Review Phase 2: Best Practices"
prompt: |
  Review HelmRelease best practices for PR.

  STANDARDS:
  - Chart: bjw-s/app-template with pinned version
  - Annotations: reloader.stakater.com/auto: "true"
  - Probes: livenessProbe + readinessProbe
  - Resources: requests + limits
  - Security: securityContext
  - Routes: envoy-internal or envoy-external parentRef (external exposure must be intentional)
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
  9. Check API versions and Flux CRD fields against the cluster's CRDs
  10. Check dependsOn has no cycles

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-2-best-practices.md.
```

## Phase 3 — Security

```yaml
subagent_type: general-purpose
description: "PR Review Phase 3: Security"
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
  6. Check RBAC and network policies against peer apps

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-3-security.md.
```

## Phase 4 — Architecture

```yaml
subagent_type: general-purpose
description: "PR Review Phase 4: Architecture"
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

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-4-architecture.md.
```

## Phase 5 — Validation

```yaml
subagent_type: general-purpose
description: "PR Review Phase 5: Validation"
prompt: |
  Run validation tools for GitOps Kubernetes PR.

  TOOLS:
  - flate test all: renders Kustomizations + HelmReleases with the real Helm/Kustomize SDKs (catches Helm template errors kustomize build can't see)
  - shellcheck: touched shell scripts

  Run `bash .agents/skills/pr-review/scripts/validate-pr.sh` for both.

  TASKS:
  1. Run shellcheck on touched scripts
  2. Run flate test all (or kustomize build + flux build if flate is unavailable)

  OUTPUT to .agents/pr-review/pr-${PR_ID}/phase-5-validation.md.
```
