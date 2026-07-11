---
name: debug-cluster
description: >-
  Diagnose and resolve Kubernetes cluster issues using structured fact gathering and 5-Whys
  root cause analysis.

  user: "pod CrashLoopBackOff" → describe pod, logs, events; GitOps fix in repo
  user: "HelmRelease not reconciling" → flux get/describe, reconcile with source
  user: "service unreachable" → endpoints, HTTPRoute, workload health
  user: "backup mover failing" → often delegate with backup-restore context

  Use for pod failures, OOM, Flux reconciliation failures, unreachable services, or node issues.
compatibility: Requires cluster access via ToolHive/kubectl; `flux` and optional `talosctl` per AGENTS.md. Live reconcile/apply needs user approval.
---

# Debug cluster

Structured debugging with parallel fact gathering; delegate deep 5-Whys or log mining to subagents when useful.

## When to delegate

| Task | Approach |
|------|----------|
| 5-Whys / log pattern analysis | Subagent |
| Status, events, single describe | Inline |
| Flux overview | MCP or `flux get` inline |

## Quick diagnosis

**Parallel gather:**

- Kubernetes resources (pods, deployments, services, events)
- Logs (current + previous containers)
- Flux instance when GitOps-related
- Historical/crashed-pod logs and firing alerts: observability MCP group (Grafana + victoria-logs)

```bash
kubectl get pods -n <ns> -o wide
kubectl get events -n <ns> --sort-by='.lastTimestamp'
kubectl describe pod <pod> -n <ns>
```

## Symptom map

| Symptom | Check |
|---------|-------|
| CrashLoopBackOff | describe, logs, previous container |
| OOMKilled | exit code 137, limits |
| ImagePullBackOff | image, registry auth, pull secrets |
| Pending | node resources, taints, PV binding |
| Flux stuck | `flux get all -A`, GitRepository status |

## 5-Whys subagent

When root cause is unclear, spawn a subagent with gathered facts and ask for five whys ending in a GitOps-fixable cause (config typo, wrong secret ref, chart values, etc.).

## Fix patterns (GitOps first)

| Issue | Fix |
|-------|-----|
| Config not picked up | Reloader annotation on workload |
| HelmRelease stuck | Delete HR; Flux recreates from git |
| Wrong image/tag | values.yaml in repo |
| PVC issues | storage class, events, VolSync path |

Avoid `kubectl edit` except ephemeral tests.

## Flux

```bash
flux get all -A --status-selector ready=false
flux reconcile kustomization <name> --with-source
```

MCP when available: `get_flux_instance`, `reconcile_flux_kustomization`. ToolHive tools are group-prefixed in-session (e.g. `flux-operator_reconcile_flux_kustomization`); resolve the real name from the session tool list before calling.

## Talos (rare)

Node NotReady, kubelet, kernel: `talosctl --nodes <ip> containers -k`, `logs kubelet`, `dmesg`.

## Validation

```bash
kubectl get pods -n <ns>
flux get helmreleases -A
```

## Cross-referencing with kubesearch

When troubleshooting an app with a common chart (cert-manager, postgres, nginx, etc.), use **kubesearch** to find how other homelab clusters configure the same component:

| Symptom | Kubesearch query |
|---------|-----------------|
| Wrong values/config | `kubesearch_grep_values(query: "<key>: <value>")` — see how others set the same field |
| Missing resource limits | `kubesearch_grep_values(query: "resources:")` + filter to your app |
| Probes failing | `kubesearch_grep_values(query: "startupProbe")` + filter to your chart |
| Persistence issues | `kubesearch_grep_values(query: "storageClass: ceph-block")` — compare PVC patterns |
| Image not found | `kubesearch_search_images(query: "<image-name>")` — see tags used by others |

Use `kubesearch_get_release` to drill into deployments of the same chart and compare complete `spec.values` blocks, then `repo_clone`/`repo_read_file` to inspect the full manifest.

## Related skills

- [backup-restore](../backup-restore/SKILL.md) — PVC recovery
- [add-app-to-cluster](../add-app-to-cluster/SKILL.md) — new deployments
- [k8s-at-home-research](../k8s-at-home-research/SKILL.md) — finding homelab config examples
- [prometheus-cluster-health](../prometheus-cluster-health/SKILL.md) — alerts, CPU/memory hotspots

Format reference: [agentskills.io](https://agentskills.io/specification).
