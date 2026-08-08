# Phase 2: Kubernetes Manifest Quality
**Completed:** 2026-08-06

## Findings

The repo is close to clean on this pass. Deprecated APIs, `:latest`/mutable image
tags, and legacy `Ingress` objects are all absent. Critical-path infra (DNS,
gateway, database, cache) is correctly redundant. The few real issues are
namespace-hygiene and label nits, not correctness bugs.

- **Removed APIs**: zero hits for `extensions/v1beta1`, `networking.k8s.io/v1beta1`
  Ingress, `policy/v1beta1` PSP, `autoscaling/v2beta2`, `apiextensions.k8s.io/v1beta1`
  across `kubernetes/`, `bootstrap/`, `talos/`.
- **Ingress vs Gateway API**: zero `kind: Ingress` objects and zero `ingress-nginx`
  references anywhere in scope. All external/internal traffic runs through
  `kubernetes/apps/network/envoy-gateway/app/envoy.yaml` (Gateway API, 4 `Gateway`
  objects) with 17 `HTTPRoute` resources cluster-wide. Migration is complete —
  no EOL ingress-nginx risk.
- **Image hygiene**: no `:latest`, no bare (untagged) images, no mutable tags
  (`:main`/`:stable`/`:edge`/`:nightly`/`:rolling`) found. Every `OCIRepository`
  and app-template `image.tag` is semver or digest pinned. Note: `hermes-agent`
  (`kubernetes/apps/ai/hermes/app/helmrelease.yaml:32`) is now pinned to
  `v2026.8.3@sha256:...`, not the `:main` rolling tag recorded in prior memory —
  that risk appears already resolved.
- **Resources**: every `HelmRelease` with a `controllers:` block sets `resources:`
  — no container found with zero requests. Two files
  (`kubernetes/apps/flux-system/flux-instance/app/helmrelease.yaml:50,66` and
  `kubernetes/apps/ai/litellm/app/helmrelease.yaml:18`) set `limits.memory` with
  no explicit `requests.memory`. Not flagged as a QoS bug: the Kubernetes API
  server defaults `requests` to the `limits` value per-resource when requests
  are omitted, so these containers are effectively Guaranteed for memory, not
  degraded. Cosmetic only — explicit requests would just be clearer to a reader.
- **Replicas / SPOF (critical-path infra only)**: CoreDNS = 2
  (`kubernetes/apps/kube-system/coredns/app/helmrelease.yaml:16`), k8s-gateway = 2
  (`kubernetes/apps/network/k8s-gateway/app/helmrelease.yaml:14`), envoy-gateway
  control plane = 2 (`kubernetes/apps/network/envoy-gateway/app/helmrelease.yaml:17`)
  with the actual proxy running as a DaemonSet on every node
  (`kubernetes/apps/network/envoy-gateway/app/envoy.yaml:14`), CloudNativePG
  cluster = 3 instances + redundant poolers
  (`kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml:11`), Dragonfly =
  3 replicas + PDB (`kubernetes/apps/database/dragonfly/cluster/cluster.yaml:6`).
  No SPOF found on genuinely critical-path infra.
- **Probes**: no `probes.*.enabled: false` found anywhere. Slow-starting
  workloads already carry deliberate startup/liveness tuning: Jellyfin
  (`kubernetes/apps/media/jellyfin/app/helmrelease.yaml:53-56`, startup
  failureThreshold 30), Immich server + machine-learning (both have startup
  probes), Hermes (custom startup probe, failureThreshold 30), and the SGLang
  `InferenceService` (`kubernetes/apps/ai/llmkube/models/qwen36-27b-sglang.yaml:263-285`)
  which deliberately makes liveness inert (`exec: ["true"]`, periodSeconds 3600)
  and relies on a 120-failure/15s startup probe so long model loads can't trigger
  a restart — a well-reasoned pattern, not a gap.
- **Graceful shutdown**: only 5 manifests set `terminationGracePeriodSeconds`
  explicitly (qbitrr 30s, Hermes 90s, xmrig 15s, xmrig-guard 15s) — everything
  else relies on the 30s default. Nothing in scope looks clearly undersized:
  the database/cache tier (CNPG, Dragonfly) is operator-managed and the
  remaining workloads are stateless HTTP services with fast shutdown paths
  (e.g. Jellyfin adds a 5s `preStop` sleep on top of the default, which covers
  its case). No action item here.
- **Raw (non-app-template) manifests**: only one raw Deployment-style workload
  exists in scope — `kubernetes/apps/kube-system/amdgpu-undervolt.yaml` (a
  `DaemonSet`). It has `app.kubernetes.io/name` but no
  `app.kubernetes.io/component` label, and no liveness/readiness probes. Low
  severity: it's a one-shot sysfs-tuning daemon that `sleep infinity`s after
  applying settings, so a probe would add little value.
- **Namespace hygiene**: the literal Kubernetes `default` namespace hosts real
  production apps — nextcloud, immich, searxng, karakeep, homepage,
  changedetection, dumbassets, picoshare, obico, spoolman, it-tools
  (`kubernetes/apps/default/kustomization.yaml:4`). This is against the general
  best practice of never using `default` for real workloads, but it's clearly
  deliberate and consistently governed here — it has its own `CiliumNetworkPolicy`
  (`kubernetes/apps/kube-system/network-policies/app/deny-apiserver-egress.yaml`,
  the `namespace: default` block) and database ingress rules
  (`kubernetes/apps/database/cloudnative-pg/cluster` ingress policy) explicitly
  reference it as a first-class namespace. Flagging as a naming/convention nit
  only, not a functional bug — renaming it now would be a wide, low-value churn
  for a working setup.
- Immich (`kubernetes/apps/default/immich/ks.yaml`) is commented out of
  `kubernetes/apps/default/kustomization.yaml:9` — dead reference left in the
  tree. Not scored as an issue (deliberate disable), but worth a cleanup pass
  if it's staying off long-term.

## Issues Found
| Severity | Resource | Location | Issue | Recommendation |
|----------|----------|----------|-------|----------------|
| Low | amdgpu-undervolt DaemonSet | `kubernetes/apps/kube-system/amdgpu-undervolt.yaml:6-16` | Raw manifest missing `app.kubernetes.io/component` label | Add `app.kubernetes.io/component: gpu-tuning` (or similar) to metadata and pod template labels |
| Low | default namespace | `kubernetes/apps/default/kustomization.yaml:4` | Literal Kubernetes `default` namespace used for ~11 production apps instead of a dedicated namespace | Cosmetic/convention only; leave as-is unless doing a larger namespace reorg — not worth churn on its own |
| Low | flux-instance controllers | `kubernetes/apps/flux-system/flux-instance/app/helmrelease.yaml:50,66` | `limits.memory` set with no explicit `requests.memory` | Optional clarity fix: add matching `requests.memory` (k8s already defaults it to the limit value, so this is style-only) |
| Low | litellm | `kubernetes/apps/ai/litellm/app/helmrelease.yaml:17-19` | Same pattern: `limits.memory` without explicit `requests.memory` | Same as above, style-only |
| Info | hermes-agent image | `kubernetes/apps/ai/hermes/app/helmrelease.yaml:31-32` | Previously-flagged `:main` rolling tag risk (accepted exception per prior review) now appears resolved — image is digest-pinned | No action; confirms the prior accepted risk is gone |
| Info | immich | `kubernetes/apps/default/kustomization.yaml:9` | `# - ./immich/ks.yaml` left commented out | Remove if immich is staying disabled long-term, to avoid stale references |

## Action Items
- None blocking. Optional: add `app.kubernetes.io/component` to `amdgpu-undervolt.yaml`.
- Optional: add explicit `requests.memory` to flux-instance and litellm for readability (no functional effect).
- Optional cleanup: drop the commented-out immich `ks.yaml` reference if it's not coming back soon.

## Summary Stats
- Total issues: 6
- Critical: 0 | High: 0 | Medium: 0 | Low: 4 | Info: 2
