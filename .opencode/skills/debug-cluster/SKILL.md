---
name: debug-cluster
description: |
  Diagnose and resolve Kubernetes cluster issues. Use when: pod failures (CrashLoopBackOff, OOM),
  Flux/HelmRelease reconciliation failures, services unreachable, node issues. Uses 5-Whys root cause
  analysis; delegate 5-Whys or log analysis to subagents when useful. Prefer ToolHive MCP; use Talos only when needed.
---

# Debug Cluster

Structured debugging with parallel fact gathering and subagent-powered root cause analysis.

## When to Delegate to Subagents

| Task | Approach | Reason |
|------|----------|--------|
| 5-Whys root cause analysis | **Subagent spawn** | Complex, iterative reasoning |
| Log pattern analysis | **Subagent spawn** | Independent, time-consuming |
| Simple status checks | **Inline** | Fast, direct |
| Resource retrieval | **Inline** | Direct MCP/kubectl calls |

---

## Quick Diagnosis

### 1. Gather Facts (PARALLEL)

Spawn subagent for logs while checking status inline:

```
PARALLEL:
- get_kubernetes_resources (pods, deployments, services, events)
- get_kubernetes_logs (current + previous containers)
- get_flux_instance (if Flux issue)
```

Or kubectl fallback:

```bash
kubectl get pods -n <ns> -o wide
kubectl get events -n <ns> --sort-by='.lastTimestamp'
kubectl describe pod <pod> -n <ns>
```

---

### 2. Identify Symptoms

| Symptom | Check |
|---------|-------|
| CrashLoopBackOff | `describe pod`, logs, previous container |
| OOMKilled | `get pod -o yaml` → `exitCode: 137` |
| ImagePullBackOff | image name, registry auth, secrets |
| Pending | node resources, taints, PV binding |
| Flux stuck | `flux get all -A`, GitRepository status |

---

## 5-Whys Analysis Subagent

When root cause isn't obvious, spawn subagent:

```
background_task:
  agent: "debug-analyzer"
  description: "5-Whys root cause analysis for <issue>"
  prompt: |
    Analyze this K8s issue using 5-Whys:

    Facts gathered:
    - Pod: <name> in namespace <ns>
    - Status: <status>
    - Events: <key events>
    - Logs: <relevant log lines>

    Apply 5-Whys:
    1. Why is <symptom> happening?
    2. Why <answer to 1>?
    3. Why <answer to 2>?
    4. Why <answer to 3>?
    5. Why <answer to 4>? → Root cause

    Output: Root cause + fix recommendation
```

Example 5-Whys chain:

1. Why pod failing? → Container restarts
2. Why restarting? → Exit code 1
3. Why exit 1? → Cannot connect to DB
4. Why no connection? → Wrong ConfigMap value
5. Why wrong value? → Git typo → **Fix in Git**

---

## Fix Patterns

Prefer GitOps fixes:

| Issue | GitOps Fix |
|-------|------------|
| Config not applied | Add `reloader.stakater.com/auto: "true"` annotation |
| HelmRelease stuck | Delete HR, Flux recreates |
| Image wrong | Fix in values.yaml, commit |
| PVC issues | Check storage class, events |

Avoid `kubectl edit` except for testing.

## Flux Debugging

Find failing resources:

```bash
flux get all -A --status-selector ready=false
```

Reconcile with source:

```bash
flux reconcile kustomization <name> --with-source
flux reconcile source git flux-system
```

Use MCP when available:

- `get_flux_instance` for overview
- `reconcile_flux_kustomization` for manual trigger

## Talos (Rare Cases Only)

Only use when: node NotReady, kubelet issues, kernel panics.

Check Kubernetes pods on node:

```bash
talosctl --nodes <ip> get kubernetespods
```

Check kubelet logs:

```bash
talosctl --nodes <ip> logs --namespace=kubelet
```

Check kernel messages:

```bash
talosctl --nodes <ip> dmesg
```

## Validation

Verify pod status:

```bash
kubectl get pods -n <ns>
```

Verify Flux releases:

```bash
flux get helmreleases -A
```

---

## Related

- **backup-restore**: PVC/data recovery
- **add-app-to-cluster**: Deploying new apps
- **AGENTS.md**: `kubeconform`, validation commands
