# Phase 4: Helm/Kustomize Quality
**Completed:** 2026-08-06

## Findings

### Corrections to detected context
- **No real `HelmRepository` resource exists in scope.** The single `grep -rl "kind: HelmRepository"` hit (`kubernetes/apps/observability/kube-state-metrics/app/helmrelease.yaml`) is a false positive: `HelmRepository` only appears as a string inside an RBAC `resources:` list and inside a `customResourceState` metric `groupVersionKind`, never as `^kind: HelmRepository`. Verified with `awk '/^kind: HelmRepository$/'` across every yaml file — zero matches. **The OCI migration is 100% complete**, not "essentially done."
- **Zero local `Chart.yaml` files exist under `kubernetes/` or `bootstrap/`** (the only 2 in the whole checkout live under `.claude/worktrees/toolhive-5891/`, an out-of-scope stale worktree). The "Local charts" review task is N/A for this repo.

### HelmRelease quality
- **Versioning is not via `chart.spec.version` at all** — every one of the 109 `HelmRelease` objects uses `chartRef: {kind: OCIRepository, ...}`, and the corresponding `OCIRepository.spec.ref.tag` (e.g. `kubernetes/apps/observability/kube-state-metrics/app/ocirepository.yaml:13` → `tag: 8.1.3`) is the pin point, managed by Renovate. Bootstrap's Helmfile releases (`bootstrap/helmfile.d/01-apps.yaml`, `00-crds.yaml`) are also all exact-pinned semver, no ranges. No loose-range findings.
- **`install`/`upgrade.remediation` and `driftDetection` are not missing** — they're injected cluster-wide by `kubernetes/flux/cluster/ks.yaml:60-76`, a `patches:` block targeting every rendered `HelmRelease` (`upgrade.remediation.retries: 2`, `strategy: RemediateOnFailure`, `rollback.cleanupOnFail: true`, `install.crds: CreateReplace`). Grepping individual `helmrelease.yaml` files for `remediation:` correctly finds ~0 — that's by design, not a gap. `driftDetection` itself is genuinely absent from both the per-app files and the cluster patch — worth adding to the cluster patch once (single edit point), not per-app.
- **`interval` is inconsistent (44× `1h` vs 27× `30m`)** with no discernible rule tying it to app criticality (e.g. `cloudnative-pg` and `sonarr-exporter` are both `30m`; `jellyfin` and `flaresolverr` are both `1h`, one core one not). Low severity — doesn't affect correctness, just drift from a convention that was never written down.
- **`timeout` is set explicitly in only 3 of 109 HelmReleases** (`grep -rh "  timeout:"` → 2× `15m`, 1× `10m`); everything else relies on the 5m default injected by the cluster patch. Not a bug, just worth confirming the 3 exceptions (large/slow charts) are intentional rather than copy-paste leftovers.
- **`OCIRepository` digest pinning**: 151 of 152 `OCIRepository` objects pin by `ref.tag` only (Renovate-managed), 1 uses `digest:`, and `spec.verify` (cosign) is used nowhere. This is consistent repo-wide and matches how Renovate automates chart bumps here — flagging all 151 individually would be noise. If cosign verification is ever wanted, it's a single addition to the OCIRepository template pattern, not a per-app fix.

### Kustomize quality
- **No structural issues found.** All `kustomization.yaml` under scope have `apiVersion`; the 8 "missing `kind: Kustomization`" hits from a naive grep are `kind: Component` (`kubernetes/components/*/kustomization.yaml`), which is correct — Components don't carry `kind: Kustomization`. False positive, verified by reading each file.
- **Zero broken `resources`/`components`/`bases` references** — verified programmatically (parsed all 152 `kustomization.yaml` under `kubernetes/`/`bootstrap/`, resolved every path relative to its file). No dead references.
- **No `vars:` usage** (already migrated / never used) and **no `helmChartInflationGenerator` usage** — clean on both.
- **No `namePrefix`/`nameSuffix` usage anywhere** — nothing to be inconsistent about.
- **Unescaped `${VAR}` in ConfigMap literals**: checked every `kind: ConfigMap` containing `${...}`. All instances (`kubernetes/components/nfs-media/configmap.yaml:8-9`, HelmRelease `env:`/`route.hostnames` blocks like `kubernetes/apps/media/radarr/app/helmrelease.yaml:37-38,106`) are *intended* Flux postBuild substitution targets (`${TRUENAS_IP}`, `${RADARR_API_KEY}`, `${SECRET_DOMAIN}`, etc.), not accidental literals. One harmless case: `kubernetes/components/common/cluster-settings.yaml:22` has `${TRUENAS_IP}` inside a YAML *comment* — cosmetically odd (would get substituted away like everything else in this ConfigMap) but has zero functional effect since it's a comment. Not worth a fix.
- **Dead child-ks config (the documented "parent patch replaces lists" hazard) does NOT occur here** — grepped every `kubernetes/apps/**/ks.yaml` for `timeout:`, `retryInterval:`, `postBuild.substituteFrom`, and `patches:`: zero child ks.yaml files set any of these. The cluster-level patch in `kubernetes/flux/cluster/ks.yaml` is the sole source for all of them, so there's no conflicting/overridden config to clean up. This repo already avoids the trap.

### Deduplication clusters (top 3, by evidence)
1. **`defaultPodOptions.securityContext` (uid/gid 568 block)** — duplicated verbatim (runAsUser/runAsGroup/fsGroup: 568, runAsNonRoot, fsGroupChangePolicy, seccompProfile) across 18 HelmReleases: `qbittorrent`, `seerr`, `prowlarr`, `unpackerr`, `sonarr-exporter`, `qui`, `sonarr`, `radarr`, `opnsense-exporter`, `kopiur`, `immich/server`, `jellyfin`, `nextcloud-exporter`, `recyclarr`, `bazarr`, `qbittorrent-exporter`, `prowlarr-exporter`, `kopia` (all under `kubernetes/apps/**/app/helmrelease.yaml`). ~6-8 lines each ≈ **~120 lines**. The repo already has the exact mechanism to fix this: `kubernetes/components/affinity-control-1/configmap.yaml` proves a `valuesFrom: [{kind: ConfigMap, name: affinity-control-1}]` pattern works for merging shared `defaultPodOptions.*` into HelmRelease values (used live in `kubernetes/apps/media/jellyfin/app/helmrelease.yaml:10-13`). A `common-podsecurity` (or similar) ConfigMap + `valuesFrom` entry would collapse ~120 lines to one ~10-line component + one 3-line reference per app.
2. **`route.app.parentRefs` envoy reference block** — the 3-line `parentRefs: [{name: envoy-internal, namespace: network}]` (or `envoy-external`) stanza is repeated in 30+6 = 36 HelmReleases (e.g. `kubernetes/apps/media/sonarr/app/helmrelease.yaml` under `route: app: parentRefs:`). ≈ **~100 lines**. Same `valuesFrom` ConfigMap-merge mechanism as #1 would work (two variants: internal/external), since Helm values from multiple `valuesFrom` sources deep-merge.
3. **Interval/timeout/remediation config is already correctly centralized** via the `kubernetes/flux/cluster/ks.yaml` patch (see above) — no dedup opportunity left there, it's the model the other two clusters should copy.

Probe blocks (`liveness`/`readiness`/`startup`) were considered but **not** counted as a dedup cluster: each *arr app's health endpoint path and port differ enough (some `/ping`, some `/health`, different ports) that a shared ConfigMap would need per-app overrides anyway, and each file already uses in-file YAML anchors (`&probes`/`*probes`) to avoid the smaller, safer duplication. Not a genuine win.

## Issues Found
| Severity | Type | Location | Issue | Recommendation |
|----------|------|----------|-------|----------------|
| Low | HelmRelease | 44 files at `interval: 1h` vs 27 at `interval: 30m` (e.g. `kubernetes/apps/database/cloudnative-pg/app/helmrelease.yaml` vs `kubernetes/apps/media/jellyfin/app/helmrelease.yaml`) | No documented rule for which apps get which reconcile interval | Either document the split (e.g. "actively-tuned apps get 30m") or standardize |
| Low | HelmRelease | `kubernetes/flux/cluster/ks.yaml:60-76` | `driftDetection` is not set in the cluster-wide HelmRelease patch (only remediation/rollback/crds are) | Add `driftDetection: {mode: enabled}` once, to the same nested patch — benefits all 109 HelmReleases in one edit |
| Low | Kustomize | `kubernetes/components/common/cluster-settings.yaml:22` | Literal `${TRUENAS_IP}` inside a YAML comment gets swept up by Flux postBuild substitution along with the real values | Cosmetic only; drop the `${...}` from the comment text if it's ever touched, not worth a dedicated fix |
| Medium | Helm values duplication | 18 `app/helmrelease.yaml` files (see cluster #1 above) | `defaultPodOptions.securityContext` (568/568) block duplicated ~120 lines total | Extract to a shared `ConfigMap` + `valuesFrom`, following the existing `affinity-control-1` component pattern |
| Medium | Helm values duplication | 36 `app/helmrelease.yaml` files (see cluster #2 above) | `route.app.parentRefs` envoy-internal/external block duplicated ~100 lines total | Same `valuesFrom` ConfigMap-merge pattern, two small ConfigMaps (internal/external) |

## Action Items
1. Add `driftDetection: {mode: enabled}` to the single cluster-wide HelmRelease patch in `kubernetes/flux/cluster/ks.yaml` — one edit, applies everywhere.
2. Create a `kubernetes/components/common-podsecurity` (naming TBD) ConfigMap mirroring `affinity-control-1`'s pattern for the uid/gid 568 `defaultPodOptions.securityContext` block; migrate the 18 identified HelmReleases to `valuesFrom`.
3. Create 1-2 small ConfigMaps (`route-envoy-internal`, `route-envoy-external`) for the `parentRefs` stanza; migrate the 36 identified HelmReleases to `valuesFrom`.
4. Optional/low priority: document why some apps use `interval: 30m` vs `1h` so it reads as a convention rather than drift.

## Summary Stats
- Total issues: 5
- Critical: 0 | High: 0 | Medium: 2 | Low: 3
- Deduplication opportunity: ~220 lines across 2 concrete clusters (uid/gid securityContext, envoy parentRefs), using a pattern already proven in this repo (`affinity-control-1` component)
- False positives corrected from detected context: HelmRepository count (1 → 0, migration is 100% complete), local Chart.yaml count (4 → 0 in scope)
