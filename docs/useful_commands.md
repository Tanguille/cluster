# Useful Commands to Debug Kubernetes

## Pod management

```bash
# Restart a deployment
kubectl rollout restart deployment -n <namespace> <deployment-name>


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

```bash
# Check mounted storage usage
kubectl exec -it -n <namespace> deployment/<deployment-name> -- df -h /path

# List persistent volume claims
kubectl get pvc --all-namespaces

# Execute commands in a pod
kubectl exec -it -n <namespace> deployment/<deployment-name> -- <command>
```
