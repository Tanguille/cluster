# Useful Commands to Debug Kubernetes

# QOL

<https://github.com/ragrag/kubectl-autons>

```bash
# Auto-namespace
kubectl autons
```

## Pod management

```bash
# Restart a deployment
kubectl rollout restart deployment -n <namespace> <deployment-name>

# Scale a deployment
kubectl scale deployment -n <namespace> <deployment-name> --replicas=<replicas>

# Create a debugging pod with networking tools
kubectl run tmp-shell --rm -i --tty --image nicolaka/netshoot -- /bin/bash

# Delete a pod
kubectl delete pod <pod-name>
```

## Logs

```bash
# Stream logs from a deployment
kubectl logs -n <namespace> deployment/<deployment-name> -f

# View deployment logs
kubectl logs -n <namespace> deployment/<deployment-name>

# Get detailed information about a deployment
kubectl describe -n <namespace> deployment/<deployment-name>
```

## Networking

```bash
# List all network policies in a namespace
kubectl get networkpolicies -n <namespace>

# Get all ingress resources across namespaces
kubectl get ingress -A

# List all services in a namespace
kubectl get services -n <namespace>

# Get endpoints across all namespaces
kubectl get -A endpoints

# Test internal service connectivity from debug pod
curl http://<service-name>.<namespace>.svc.cluster.local
```

## Storage

<https://github.com/clbx/kubectl-browse-pvc>

```bash
# Browse PVCs
kubectl browse-pvc

# Check mounted storage usage
kubectl exec -it -n <namespace> deployment/<deployment-name> -- df -h /path

# List persistent volume claims
kubectl get pvc --all-namespaces

# Execute commands in a pod
kubectl exec -it -n <namespace> deployment/<deployment-name> -- <command>
```

## Troubleshooting Failed HelmReleases

When a HelmRelease is stuck or failing to deploy (e.g., qBittorrent case):

```bash
# 1. Check HelmRelease status and events
kubectl describe helmrelease <release-name> -n <namespace>

# 2. Delete the failed HelmRelease to allow Flux to redeploy
kubectl delete helmrelease <release-name> -n <namespace>

# 3. Clean up any lingering resources
kubectl delete deployment <deployment-name> -n <namespace>
kubectl delete service <service-name> -n <namespace>

# 4. Force Flux to reconcile and redeploy
flux reconcile kustomization <kustomization-name> --with-source

# 5. Monitor the new deployment
kubectl get pods -n <namespace> -w
```

Common issues to check if problems persist:
- Resource constraints
- Volume mount issues
- Network connectivity
- Init container configuration
- Context deadline exceeded errors (pod taking too long to become ready)

