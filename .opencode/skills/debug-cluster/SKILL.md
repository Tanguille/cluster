---
name: debug-cluster
description: |
  Diagnose and resolve Kubernetes cluster issues using structured debugging. Use when:
  - Debugging pod failures, CrashLoopBackOff, OOM kills
  - Flux reconciliation issues, HelmRelease failures
  - Service unreachable, network issues
  - Cluster anomalies, node issues
  Includes 5-Whys root cause analysis, ToolHive MCP integration, and Talos commands for rare cases.
  Note: Most issues only need kubectl commands - use Talos commands only when explicitly needed.
---

# Debug Cluster

Structured debugging with 5-Whys root cause analysis.

## Workflow

### 1. Gather Facts

Use ToolHive MCP when available:
```
get_kubernetes_resources (pods, deployments, services in namespace)
get_kubernetes_logs (pod logs, previous container logs)
get_flux_instance (Flux objects, Kustomization/HelmRelease status)
```

Or kubectl:
```bash
kubectl get pods -n <ns> -o wide
kubectl describe pod <pod> -n <ns>
kubectl logs <pod> -n <ns> --previous
kubectl get events -n <ns> --sort-by='.lastTimestamp'
```

### 2. Identify Symptoms

Common patterns:
- **CrashLoopBackOff**: Check `kubectl describe`, logs, previous container
- **OOMKilled**: Check `kubectl get pod -o yaml` for `lastState.terminated.exitCode: 137`
- **ImagePullBackOff**: Check image name, registry auth
- **Pending**: Check node resources, PV binding
- **Flux not reconciling**: Check `flux get all -A`, GitRepository status

### 3. Apply 5-Whys

Drill to root cause:
1. Why is the pod failing? → Container keeps restarting
2. Why is it restarting? → Exit code 1
3. Why exit code 1? → Cannot connect to database
4. Why cannot connect? → Wrong connection string in ConfigMap
5. Why wrong string? → Git commit had typo → Fix in Git

### 4. Propose Fix

Prefer GitOps (fix in repo) over manual intervention:
- Update manifests in Git → `task reconcile`
- Or delete stuck resource → Flux recreates
- Avoid `kubectl edit` unless testing

### 5. Validate

```bash
kubectl get pods -n <ns>
flux get helmreleases -A
```

## Flux Debugging

```bash
# Check Flux status
flux get all -A --status-selector ready=false

# Reconcile specific resource
flux reconcile kustomization <name> --with-source
flux reconcile source git flux-system

# Check GitRepository
flux get sources gitrepository
```

## Talos Debugging (Rare)

Most issues don't need Talos. Only use when:
- Node not ready
- kubelet issues
- Kernel panic

```bash
# Node status
talosctl --nodes <ip> get kubernetespods

# System logs
talosctl --nodes <ip> logs --namespace=kubelet

# dmesg
talosctl --nodes <ip> dmesg

# Service status
talosctl --nodes <ip> services
```

## Common Fix Patterns

| Issue | Fix |
|-------|-----|
| ConfigMap change not applied | Annotate with `reloader.stakater.com/auto: "true"` or `kubectl rollout restart` |
| HelmRelease stuck | Delete HelmRelease (Flux recreates) |
| PVC stuck | Check events, storage class |
| ImagePullBackOff | Check image name, registry secrets |

## Related

- **backup-restore**: For PVC/data recovery
- **useful_commands.md**: Quick kubectl reference
- **AGENTS.md**: Validation commands (`kubeconform`, `flux-local test`)
