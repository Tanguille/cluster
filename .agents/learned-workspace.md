# Learned Workspace Facts

**When to use:** HTTPRoute, ToolHive, MCPServer, Flux substituteFrom, cluster-secrets, Talos, Reloader, in-cluster URL, CNPG, Rook, or continual learning.

Stable, non-sensitive facts about this cluster and tooling.

- For continual learning, update `.agents/learned-preferences.md` or this file; keep full detail in `.agents/` rather than `AGENTS.md`.
- Use in-cluster service URLs (e.g. `http://service.namespace.svc.cluster.local:port`) for pod-to-pod calls to avoid external DNS or hairpinning.
- Internal HTTPRoutes use parentRef name `envoy-internal`; k8s-gateway serves DNS for routes attached to that gateway.

## ToolHive / MCP

- MCPServer secret-backed env vars use `spec.secrets` with `targetEnvName`; `env[].valueFrom` is unsupported. Transport values are `streamable-http` (e.g. `talos-mcp`) or `stdio` (e.g. `grafana`) — never `streamablehttp`.
- VMCP session storage uses Redis at `dragonfly.database.svc.cluster.local:6379`.

## Talos

- Talos kernel arguments belong in `schematic.yaml`; existing nodes need a schematic rebuild and Talos upgrade to receive them.
- Large Talos `/var/lib/containerd` usage is often old image layers. Configure kubelet image GC thresholds to run sooner; confirm Talos apply mode before assuming a reboot is unnecessary.

## Storage

- With `ceph-block` RWO storage, use Deployment strategy `Recreate`; RollingUpdate is unsupported.
- The app-template chart defaults to `Recreate`. If it emits `rollingUpdate` too, fix the chart, postRenderer, or patch.
- Ceph `mon_data_avail_warn` defaults to 30%. Address Talos node EPHEMERAL headroom before lowering the threshold.

## Flux

- Flux `postBuild.substituteFrom` replaces `${...}` in rendered manifests. Escape literals with Flux/Kustomize `$$` patterns, or Flux may empty unintended matches.

## CNPG

- CloudNativePG postgres16 and the barman-cloud plugin run in namespace `database`. The plugin Deployment is `barman-cloud-plugin-barman-cloud`; its Service is `barman-cloud`. To re-add an instance after its join job was deleted, delete its PVC and force-reconcile.

## Operations

- Annotate relevant deployments with Reloader (e.g. `reloader.stakater.com/auto: "true"`) so pods restart when referenced ConfigMaps or Secrets change.
- For one-shot privileged pods, use a YAML manifest and `kubectl apply -f`; complex `kubectl run --overrides` JSON is unreliable. Delete an existing pod before reapplying because pod specs are largely immutable.
- Follow KISS principles; avoid init containers or extra complexity unless needed.
