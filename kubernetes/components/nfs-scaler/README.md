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

See `kubernetes/apps/volsync-system/kopia/ks.yaml` for a complete example.

## Configuration

The component creates a ScaledObject that:

- Monitors `probe_success{instance=~".+:2049"}` from Prometheus
- Scales between 0 and 1 replicas based on NFS availability
- Uses a threshold of 1 (NFS must be available to scale up)

## Customization

To customize `maxReplicaCount`, you can patch the ScaledObject in your app's kustomization:

```yaml
patches:
  - target:
      kind: ScaledObject
      name: ${APP}
    patch: |-
      - op: replace
        path: /spec/maxReplicaCount
        value: 3  # Your desired max replicas
```
