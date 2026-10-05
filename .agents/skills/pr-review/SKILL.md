---
name: pr-review
description: >-
  Review GitOps Kubernetes PRs or local diffs: naming, HelmRelease patterns,
  SOPS/security, structure, and build validation.

  user: "Review this PR" → five parallel subagents, aggregate under .agents/pr-review/pr-<id>/
  user: "Check my app config" → phase 2 (best practices) subagent
  user: "Are secrets encrypted?" → phase 3 (security) subagent
  user: "Validate before CI" → phase 5 (validation) subagent
  user: "Review my local changes" → PR_ID=local-changes, staged + unstaged diff
  user: "Shepherd this PR / iterate until CI and review are green" → shepherd loop
    (rebase discipline, diff-grounded description, CI triage: references/pr-shepherd.md)

  Use proactively for K8s app/Flux/HelmRelease changes, infrastructure edits, or pre-commit diff review.
compatibility: Requires `git`, `mise`, `flate`, and `shellcheck` for phase 5 (falls back to `kustomize` if `flate` is unavailable); optional `gh` for PR metadata.
---

# PR review

Spawn focused subagents for GitOps Kubernetes PRs. Each phase uses clean context; outputs are isolated per review id.

## When to use

- Full PR or local diff review before merge or commit.
- Single phase (security, validation, best practices) on request.
- Pre-CI validation of YAML/Flux/Kustomize changes.

## Repository context

- Layout: `kubernetes/apps/<namespace>/<app>/` with `ks.yaml` + `app/`
- Charts: `bjw-s/app-template` (common); URLs `${SECRET_DOMAIN}`; secrets SOPS-only
- Routes: Gateway API `HTTPRoute`, parentRef `envoy-internal` / `envoy-external`
- Validation: `mise exec -- flate test all` (renders Kustomizations + HelmReleases with the real Helm/Kustomize SDKs — catches Helm template errors `kustomize build` can't see), `mise exec -- shellcheck`; both run via [scripts/validate-pr.sh](scripts/validate-pr.sh)

## Workflow

1. **Initialize** — Set `PR_ID` from URL (`2223`), branch name, or `local-changes` for git diff.
2. **Prepare directory** — `mkdir -p .agents/pr-review/pr-${PR_ID}`; for local reviews, capture staged/unstaged diffs (see [references/workflow.md](references/workflow.md)).
3. **Launch phases** — Spawn phases 1–5 in **one message** when doing a full review. Prompts: [references/phase-prompts.md](references/phase-prompts.md).
4. **Aggregate** — Merge phase reports into `pr-review-state.md` (template in [references/workflow.md](references/workflow.md)).
5. **Present** — Severity table (Critical / High / Medium / Low), blocking issues, quick wins; link to `.agents/pr-review/pr-${PR_ID}/`.

## Phase map

| Phase | Focus | Output file |
|-------|--------|-------------|
| 1 | Naming | `phase-1-naming.md` |
| 2 | HelmRelease / app patterns | `phase-2-best-practices.md` |
| 3 | SOPS, domains, securityContext | `phase-3-security.md` |
| 4 | ks.yaml + app/ structure | `phase-4-architecture.md` |
| 5 | shellcheck, kustomize, flux | `phase-5-validation.md` |
