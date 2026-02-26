# Kubernetes DNS Conflict with External Domains

## Problem

When deploying an application in Kubernetes with a Service name that matches an external domain, DNS resolution for that external domain will fail.

### Symptoms

- Pod can resolve DNS for the external domain
- But connection times out (connection refused)
- Internal cluster services with the same name take precedence

### Example

**Scenario**: Deploying `opencode` service in `ai` namespace
- Internal DNS: `opencode.ai.svc.cluster.local` resolves to `10.43.226.155` (cluster IP)
- External domain: `opencode.ai` (public website)
- **Result**: When the pod tries to connect to `opencode.ai`, Kubernetes DNS returns the internal cluster IP instead of the public IP

## Solution

Use `fullnameOverride` in HelmRelease to rename the Kubernetes Service to avoid conflict:

```yaml
spec:
  values:
    fullnameOverride: opencode-server  # Different from external domain
```

## Verification

Test DNS resolution from within a pod:

```bash
kubectl run dns-test --image=curlimages/curl:latest --rm -it -- sh
# Then inside:
nslookup opencode.ai
curl -v https://opencode.ai
```

If you see the internal cluster IP (e.g., `10.43.x.x`) instead of the public IP, you have a DNS conflict.

## Prevention

- Choose Kubernetes Service names that don't conflict with external domains you'll need to reach
- Consider using prefixes/suffixes: `opencode-server`, `opencode-app`, `opencode-internal`
- Document known external domains your applications need to reach
