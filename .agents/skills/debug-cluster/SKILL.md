---
name: debug-cluster
description: >-
  Diagnose and resolve Kubernetes cluster issues using structured fact gathering and 5-Whys
  root cause analysis. Prefer ToolHive MCP; use Talos only for node/kubelet problems.

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

MCP when available: `get_flux_instance`, `reconcile_flux_kustomization`.

## Talos (rare)

Node NotReady, kubelet, kernel: `talosctl --nodes <ip> get kubernetespods`, `logs --namespace=kubelet`, `dmesg`.

## Validation

```bash
kubectl get pods -n <ns>
flux get helmreleases -A
```

## Related skills

- [backup-restore](../backup-restore/SKILL.md) — PVC recovery
- [add-app-to-cluster](../add-app-to-cluster/SKILL.md) — new deployments

Format reference: [agentskills.io](https://agentskills.io/specification).
