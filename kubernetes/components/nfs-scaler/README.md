# NFS Scaler Component

This component uses KEDA to automatically scale deployments to 0 replicas when NFS is unavailable, and scale back up when NFS becomes available.

## How It Works

- **When NFS is available** (`probe_success{instance=~".+:2049"}` = 1): Scales to `maxReplicaCount` (default: 1)
- **When NFS is unavailable** (`probe_success` = 0 or missing): Scales to `minReplicaCount` (0)

## Usage

Add this component to your app's Kustomization:

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: your-app
spec:
  components:
    - ../../../../components/nfs-scaler
  postBuild:
    substitute:
      APP: your-app-name
  # ... rest of your config
```

## Example

See `kubernetes/apps/kopiur-system/kopia/ks.yaml` for a complete example.
