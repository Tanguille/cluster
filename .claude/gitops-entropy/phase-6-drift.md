# Phase 6: Drift & Deprecation Check
**Completed:** 2026-08-06

## Findings

**Repo hygiene:** 11 stale git worktrees exist under `.worktrees/` (plus one live one for the hermes-browser-sidecar plan under `.claude/worktrees/`) — clean these up with `git worktree remove` + branch delete. Excluded from all scanning below per scope.

**Deprecated/removed Kubernetes APIs:** none found. No `policy/v1beta1`, `extensions/v1beta1` or `networking.k8s.io/v1beta1` Ingress, `apiextensions.k8s.io/v1beta1` CRDs, deprecated RBAC/admissionregistration/HPA/flowcontrol betas anywhere in `kubernetes/`, `bootstrap/`, `talos/`.

**EOL components:** no `ingress-nginx` (gateway API / envoy-gateway is the ingress layer — good, sidesteps the EOL entirely). No Jaeger sidecars, no PodSecurityPolicy tooling, no Flux v1 CRDs (`fluxcd.io/v1alpha1`/`v1beta1`).

**Deprecated patterns:** no `last-applied-configuration` annotations, no `imagePullPolicy: Always` + `:latest` combos, no mutable tags (`:stable`/`:main`/`:edge`/`:nightly`/`:rolling`/`:develop`/`:latest`) on any container image — every app image is `tag: vX.Y.Z@sha256:...` or a numeric chart-tracked tag. `hermes-agent` (accepted-decision note in memory) is actually pinned to a sha256 digest today (`kubernetes/apps/ai/hermes/app/helmrelease.yaml:32`), not a rolling tag — the "rolling tag" memory note describes a state that's since been fixed; no action needed, just don't re-flag it as still-rolling.

Bare `app:` labels (not `app.kubernetes.io/name`) found in 3 places, but they're `podAffinity` matchLabels, not resource labels: `kubernetes/apps/ai/llmkube/models/qwen35-2b.yaml:75`, `vmcp-embedding.yaml:83`, `qwen3-embedding.yaml:76` — intentional anti-affinity keys between the two embedding models, not drift.

**Suspended Flux resources:** none. `grep -rl "suspend: true"` across `kubernetes/` and `bootstrap/` returns zero hits — no live git/cluster divergence from this vector.

**postRenderer workarounds:** only two in the whole repo, both legitimate hardening (not upstream-bug bets):
- `kubernetes/apps/default/nextcloud/app/helmrelease.yaml:28-44` — forces `automountServiceAccountToken: false` on the Deployment and CronJob (chart doesn't expose the field).
- `kubernetes/apps/security/crowdsec/bouncers/envoy/helmrelease.yaml:12-22` — adds `reloader.stakater.com/auto` annotation the chart doesn't template.
The previously-tracked llmkube PrometheusRule postRenderer workaround (memory note, chart 0.9.2) is **gone** — chart is now pinned at 0.9.14 and no postRenderer exists under `kubernetes/apps/ai/llmkube/`. Looks resolved upstream; worth a one-line changelog check next bump but not urgent.

**TODO/FIXME/HACK debt:** one real hit — `kubernetes/apps/ai/llmkube/models/qwen36-27b-vllm.yaml:146`: `liveness: exec: command: ["true"]` with a comment `# TODO: Replace this always-successful check with a real health-based liveness probe before production cutover.` This is a no-op liveness probe (always succeeds) deliberately left in place pending a real health check — flag as a known gap, not urgent since it fails safe (never kills the pod) rather than unsafe.

**Broken references:** none found.
- All `resources:` entries in every `kustomization.yaml` resolve to real files.
- All Flux `dependsOn` targets resolve to a `Kustomization` defined somewhere in the repo (cross-checked all ~90 `ks.yaml` names against every `dependsOn` reference).
- All `HTTPRoute`/`TLSRoute` `parentRefs` (`envoy-external`, `envoy-external-probe`, `envoy-internal`, `envoy-internal-tls`) resolve to Gateways defined in `kubernetes/apps/network/envoy-gateway/app/envoy.yaml`.
- Spot-checked ConfigMap/Secret references (`affinity-control-1`, `nfs-media`, `qbitrr-secrets`, immich/crowdsec anchors) — all defined in-repo, either via `kubernetes/components/*` or same-file YAML anchors.

**Mutable/unpinned chart refs:** none. Every `OCIRepository` chart ref carries a real version tag (Renovate-tracked); the few `image: {repository}` blocks with no `tag:` key (`coredns`, `victoria-metrics` alertmanager CR, `rook-ceph` operator image) inherit their tag from the chart's own pinned appVersion — chart version is itself pinned and Renovate-bumped (e.g. `rook-ceph/app/ocirepository.yaml: tag: v1.20.3`), so these aren't drift, just the standard "let the chart pick its matching image" idiom.

**Renovate coverage gaps:** none identified falling through the cracks. Custom managers cover GrafanaDashboard URLs, qBitrr's `config.toml` version string, and HuggingFace model commit SHAs in llmkube model manifests (`.renovaterc.json5:9-41`). Standard docker/helm/helmfile datasources cover everything else, including `bootstrap/helmfile.d/*.yaml` (confirmed active via recent Renovate PR history, e.g. cilium/coredns/cert-manager bumps).

**Old vs native sidecar pattern:** no native sidecars (`initContainer` + `restartPolicy: Always`) used anywhere — not a defect, just unadopted; nothing currently needs it. `karakeep`'s Chrome browser runs as a separate controller/Service (`kubernetes/apps/default/karakeep/app/helmrelease.yaml:87-93,169-170`), which is the deliberate pattern the pending `docs/hermes-browser-sidecar-isolation-plan.md` cites as precedent for isolating hermes's browser the same way — consistent, not drift.

## Untracked Plan Docs Status

| File | Work done? | Recommendation |
|------|-----------|----------------|
| `docs/hermes-browser-sidecar-isolation-plan.md` | PENDING — `hermes` HelmRelease still runs Chromium in-process with `--no-sandbox`; no separate `hermes-chrome` controller or NetworkPolicy exists yet. | Keep as-is; work not started. |
| `docs/hermes-config.md` | N/A — this is a reference doc, not a plan (runbook/gotcha content, e.g. `contextLength: 180000` matches live `qwen36-27b-sglang.yaml:179`). | Keep as-is, already the reference doc. |
| `docs/home-assistant-todo.md` | PENDING — personal checklist of manual/physical actions (Zigbee re-pair, HACS installs), entirely outside `kubernetes/` GitOps scope. | Keep as-is; not trackable from manifests. |
| `docs/llmkube-observability-gaps-plan.md` | PARTIALLY DONE — cluster-side workaround live (`rules.gpu.enabled: false`, dashboard filtering in `kubernetes/apps/ai/llmkube/app/helmrelease.yaml`); upstream chart-side work (AMD-vendor-aware alerts/panels) is in a separate fork repo and unverifiable from here. | Keep; close out once upstream PRs land and `rules.gpu.enabled: false` swaps to `rules.gpu.vendor: amd`. |
| `docs/mcp-unified-consolidation-plan.md` | DONE — every step verified live: single `groupRef: all`, no duplicate `-opt` GitHub entry, `mcpgroups`/`virtualmcpservers` collapsed to `all`/`unified`, single `mcp-unified` HTTPRoute, gatus only checks `vmcp-unified`. | Consolidate into a new `docs/toolhive-mcp-topology.md` reference doc (none exists yet), then delete the plan file. |
| `docs/toolhive-5891-call-tool-leniency-plan.md` | PENDING — targets upstream `stacklok/toolhive` repo; no evidence PR #5891 merged (toolhive-operator pin unchanged). | Keep as-is until upstream merges. |

## Action Items
1. Delete 11 stale worktrees under `.worktrees/` and their branches (repo hygiene, not urgent).
2. Consolidate `docs/mcp-unified-consolidation-plan.md` into a new `docs/toolhive-mcp-topology.md` and remove the plan file — the work it describes is fully shipped.
3. Replace the no-op liveness probe in `kubernetes/apps/ai/llmkube/models/qwen36-27b-vllm.yaml:146-151` with a real health-based check before treating that deployment as production-hardened (already flagged inline via TODO).
4. When `docs/llmkube-observability-gaps-plan.md`'s upstream PRs land, flip `rules.gpu.enabled: false` → `rules.gpu.vendor: amd` and close the plan.
5. No urgent security/compat action required elsewhere — repo is clean on deprecated APIs, EOL components, mutable tags, and broken references.

## Summary Stats
- Total issues: 6
- Critical: 0 | High: 0 | Medium: 1 | Low: 5
