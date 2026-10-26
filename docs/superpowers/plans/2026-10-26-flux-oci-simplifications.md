# Flux OCI Simplifications Implementation Plan

> **For agentic workers:** Use subagent-driven-development for bounded implementation lanes. Track progress below.

**Goal:** Adapt onedr0p/home-ops#12002 across all eligible releases and enable interruptible Helm health checks in one draft PR.

**Architecture:** Inline dedicated OCI sources in HelmRelease chart templates, retaining shared sources. Bootstrap derives chart coordinates from release manifests. Keep existing remediation policy while enabling Helm cancellation.

**Tech Stack:** Flux, Kubernetes, Helmfile, Renovate, flate.

**Spec:** User-approved scope in this conversation; latest instruction replaces the pilot with all-at-once implementation.

## Global Constraints

- Preserve chart versions, values, source settings, shared app-template sources, and generated consumers.
- Inline sources still create OCIRepository objects at runtime; no runtime resource-count reduction is claimed.
- Preserve rollback/remediation policy. No live apply, reconcile, secret edits, or push without permission.
- Work only in the isolated worktree; leave the original conflicted checkout untouched.

## Review Focus

- Shared and cross-directory consumers must not lose their source.
- Bootstrap must preserve dependency ordering and use the correct chart for CRD releases.
- Renovate must extract chart tags exactly once without misclassifying values images.
- Installed controller/schema and local tooling must support inline sources.
- Cancellation requires poller support; static checks do not prove rollback behavior during interruption.

### Task 1: Inline dedicated chart sources

- [x] Account for all OCIRepository consumers and preserve shared sources.
- [x] Move eligible source specs into spec.chart.kind: OCIRepository, preserving configuration.
- [x] Remove only replaced source files and Kustomization references.
- [x] Check source/version preservation, YAML validity, and retained references.

### Task 2: Bootstrap and dependency automation

- [x] Derive bootstrap chart URL/version from release manifests, including CRD bootstrap where applicable.
- [x] Preserve bootstrap release order and values.
- [x] Verify Renovate extraction; add only necessary compatibility configuration.
- [x] Run focused Helmfile and Renovate checks.

### Task 3: Helm cancellation and compatibility

- [x] Verify deployed controller/CRD support read-only and check upstream cancellation semantics.
- [x] Enable CancelHealthCheckOnNewRevision for helm-controller, retaining all existing gates and remediation policy.
- [x] Establish focused checks and document runtime-testing limits/known rollback risks.

### Final reconciliation

- [x] Update repository guidance to teach inline dedicated sources and shared chartRef sources.
- [x] Review combined diff and run required PR validation once after all writers finish.
- [ ] Record limitations, commit scoped changes, request push permission, and open draft PR.

## Verification record

- 57 dedicated source specs preserved exactly, including chart URL/tag; shared `app-template` and generated consumers retained.
- Installed helm-controller v1.7.1 and CRD read-only checks confirm inline OCI and poller support; no upgrade required.
- Both Helmfiles build with Helmfile 1.8.2. Renovate 44.186.2 config validation/extraction and four regex regression tests pass.
- Required PR validation passes: flate, shellcheck, secret guardrails, and 24 secret-check regression tests.
- Focused Kubernetes and bootstrap/Renovate reviews found no blocking defects.
- No live apply/reconcile or interrupted-release runtime test was performed. Upstream v1.7.0 includes the canceled-rollback fix, but canceled revisions remain failed in Helm history and cleanup of interrupted-only resources is not guaranteed.
