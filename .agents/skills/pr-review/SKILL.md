---
name: pr-review
description: >-
  Review GitOps Kubernetes PRs or local diffs: YAML format, naming, HelmRelease patterns,
  SOPS/security, structure, and build validation.

  user: "Review this PR" → six parallel subagents, aggregate under .agents/pr-review/pr-<id>/
  user: "Check my app config" → phase 3 (best practices) subagent
  user: "Are secrets encrypted?" → phase 4 (security) subagent
  user: "Validate before CI" → phase 6 (validation) subagent
  user: "Review my local changes" → PR_ID=local-changes, staged + unstaged diff

  Use proactively for K8s app/Flux/HelmRelease changes, infrastructure edits, or pre-commit diff review.
compatibility: Requires `git`, `mise`, `flate`, and `shellcheck` for phase 6 (falls back to `kustomize`/`flux` if `flate` is unavailable); optional `gh` for PR metadata.
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
- Validation: `mise exec -- flate test all` (renders Kustomizations + HelmReleases with the real Helm/Kustomize SDKs — catches Helm template errors `kustomize build` can't see), `mise exec -- shellcheck`

## Workflow

1. **Initialize** — Set `PR_ID` from URL (`2223`), branch name, or `local-changes` for git diff.
2. **Prepare directory** — `mkdir -p .agents/pr-review/pr-${PR_ID}`; for local reviews, capture staged/unstaged diffs (see [references/workflow.md](references/workflow.md)).
3. **Launch phases** — Spawn phases 1–6 in **one message** when doing a full review. Prompts: [references/phase-prompts.md](references/phase-prompts.md).
4. **Aggregate** — Merge phase reports into `pr-review-state.md` (template in [references/workflow.md](references/workflow.md)).
5. **Present** — Severity table (Critical / High / Medium / Low), blocking issues, quick wins; link to `.agents/pr-review/pr-${PR_ID}/`.

## Phase map

| Phase | Focus | Output file |
|-------|--------|-------------|
| 1 | YAML format | `phase-1-yaml-format.md` |
| 2 | Naming | `phase-2-naming.md` |
| 3 | HelmRelease / app patterns | `phase-3-best-practices.md` |
| 4 | SOPS, domains, securityContext | `phase-4-security.md` |
| 5 | ks.yaml + app/ structure | `phase-5-architecture.md` |
| 6 | shellcheck, kustomize, flux | `phase-6-validation.md` |

## Inline quick checklist

For small diffs without subagents:

- [ ] 2-space indent, LF, no trailing whitespace
- [ ] lowercase-dashes resources; `ks.yaml` + `app/` layout
- [ ] `bjw-s/app-template` pinned; Reloader annotation when needed
- [ ] Probes and resource requests/limits
- [ ] SOPS secrets; `${SECRET_DOMAIN}`; envoy route parentRefs
- [ ] `flate test all` / `shellcheck` pass on touched paths

## Progressive disclosure

- Subagent prompts: [references/phase-prompts.md](references/phase-prompts.md)
- Isolation, local diff, aggregation: [references/workflow.md](references/workflow.md)
- Expanded validation topics: [references/best-practices.md](references/best-practices.md)
- Script: [scripts/validate-pr.sh](scripts/validate-pr.sh)

Format reference: [agentskills.io](https://agentskills.io/specification).
