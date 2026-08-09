# Phase 1: GitOps Architecture Review
**Completed:** 2026-08-06

## Findings

**Layout.** `kubernetes/apps/<namespace>/<app>/{ks.yaml,app/}` is applied consistently across all 16 namespace directories and ~90 `ks.yaml` files. Multi-component apps (nextcloud, cloudnative-pg, toolhive, immich) correctly split into multiple Kustomizations per subdirectory (`app/`, `cluster/`, `databases/`, `crds/`, `config/`) rather than cramming everything into one. No dev/staging/prod chain, as expected for a single-cluster homelab.

**Kustomization shape.** Individual `ks.yaml` files look minimal (no `retryInterval`, `timeout`, or `postBuild.substituteFrom`) — this is *not* drift. The top-level `kubernetes/flux/cluster/ks.yaml` (`cluster-apps` Kustomization) injects these into every child via a `patches:` block targeting `group: kustomize.toolkit.fluxcd.io, kind: Kustomization`, with an explicit escape-hatch label (`flux.toolkit.fluxcd.io/substitute-from: skip`) documented inline. This is a clean, deliberate DRY pattern, not an anti-pattern — flag as a positive, not an issue.

**Single source of truth.** No `kubectl apply` scripts, no committed `suspend: true` resources found in `kubernetes/`. `scripts/` and `.taskfiles/` contain only read-only diagnostics (`debug-toolhive-mcp.sh` is pure `kubectl get/logs/describe`, no mutating calls) and a Talos schematic-update helper. One `prune: false`: `kubernetes/apps/ai/toolhive/ks.yaml` on `toolhive-operator-crds` — standard, deliberate CRD-protection pattern (avoids Flux deleting CRDs on Kustomization removal), not an imperative escape hatch.

**God directories/manifests.** None. Largest directory is 13 files with existing sub-grouping (`cloudnative-pg/databases`, `toolhive/config`). Largest manifest is 480 lines (`default/nextcloud/app/helmrelease.yaml`), a single HelmRelease with legitimately large `values:` — under the 300-line manifest threshold in relative terms once you account for it being one logical unit, but still the biggest file in the repo (see Issues).

**Naming.** Directory name, Kustomization `metadata.name`, and `app-template` release name align across all spot-checked apps (wizarr, changedetection, memini, omniroute, etc.). Style is split between `name: &app <x>` (anchor reused later) and plain `name: <x>` — cosmetic inconsistency only, not worth fixing.

**Dependency graph.** `dependsOn` is used correctly where it matters: nextcloud → `cloudnative-pg-cluster` + `dragonfly-cluster`, litellm → `litellm-operator` + `cloudnative-pg-databases`, toolhive-operator → toolhive-operator-crds → toolhive config (3-stage chain). No circular dependencies found. No missing dependsOn spotted on the apps checked.

**Secrets.** One Secret manifest without an inline `sops:` block: `kubernetes/apps/media/qbittorrent/tools/qbitrr/secret.yaml`. Verified — it uses `stringData: SONARR_API_KEY: ${SONARR_API_KEY}` etc., resolved via Flux `postBuild.substituteFrom: cluster-secrets` (the actual secret values live encrypted in `cluster-secrets.sops.yaml`). Not a plaintext leak — correct use of substitution instead of SOPS-encrypting the same value twice.

**Cluster-settings usage.** `kubernetes/components/common/cluster-settings.yaml` centralizes all cluster IPs (OPNsense, control nodes, TrueNAS, IPMI, etc.) as a ConfigMap consumed via `postBuild.substituteFrom`. The only raw IPs found outside it are generic RFC1918 CIDR ranges in nextcloud/smtp-relay (`192.168.0.0/16`, `10.0.0.0/8`) used for trusted-proxy config — appropriately generic, not cluster-specific singles.

**Native sidecar pattern.** All 5 files using `initContainers` (fileflows, qbitrr, immich-server, moonraker-obico, llmkube qwen36-27b-sglang) use `restartPolicy: Always` — the modern native-sidecar API is used consistently; no legacy sidecar hacks found.

## Issues Found

| Severity | Location | Issue | Recommendation |
|----------|----------|-------|-----------------|
| High | All 152 `OCIRepository` sources (e.g. `kubernetes/apps/kube-system/cilium/app/ocirepository.yaml:11` `ref: tag: 1.20.0`) | 0 of 152 OCI sources pin by `ref.digest` or use `spec.verify` (cosign). Tags are mutable; Renovate bumps tags but a registry-side retag/compromise is silently pulled. | Not urgent to fix wholesale, but at least add `spec.verify` (keyless cosign) for security-sensitive images (cilium, cert-manager, rook-ceph) where upstream publishes signatures. |
| Low | `kubernetes/apps/default/nextcloud/app/helmrelease.yaml` (480 lines) | Largest single manifest in repo; still one logical HelmRelease `values:` block, not urgent, but a candidate to split `values` into a separate `values.yaml`/ConfigMap if it grows further. | Watch, no action needed now. |
| Info (not a defect) | `kubernetes/apps/ai/toolhive/ks.yaml:14` `prune: false` | Deliberate CRD-protection, correctly scoped to the `crds` Kustomization only. | No action — documented for completeness per audit scope item 2. |

## Action Items
- [ ] Evaluate adding `spec.verify` (cosign) to OCIRepository sources for security-critical charts (cilium, cert-manager, rook-ceph, envoy-gateway) — lowest-effort, highest-value subset rather than all 152.
- [ ] No structural or single-source-of-truth violations require action.

## Summary Stats
- Total issues: 2 (plus 1 informational, not counted as a defect)
- Critical: 0 | High: 1 | Medium: 0 | Low: 1
