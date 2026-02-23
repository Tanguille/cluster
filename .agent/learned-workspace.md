# Learned Workspace Facts

**When to use:** HTTPRoute, ToolHive, MCPServer, Flux substituteFrom, Talos, Reloader, in-cluster URL.

Stable, non-sensitive facts about this cluster and tooling.

- Use in-cluster service URLs (e.g. `http://service.namespace.svc.cluster.local:port`) for pod-to-pod calls (e.g. OpenCode → MCP gateway, OpenCode → Ollama) to avoid external DNS or hairpinning.
- Annotate deployments with Reloader (e.g. `reloader.stakater.com/auto: "true"`) so pods restart when referenced ConfigMaps change.
- Follow KISS principles; avoid init containers or extra complexity unless needed (e.g. config not seen due to mount order).
- Internal HTTPRoutes must use parentRef name `envoy-internal` (not `internal`); k8s-gateway serves DNS for routes attached to that gateway.
- ToolHive MCPServer: use `spec.secrets` with `targetEnvName` for secret-backed env vars; `env[].valueFrom` is not supported by the CRD.
- ToolHive MCPServer transport must be `streamable-http` (with hyphen); `streamablehttp` is invalid per CRD.
- Talos kernel args (e.g. `talos.dashboard.disabled=1`) go in schematic.yaml; existing nodes need schematic rebuild and Talos upgrade to pick them up.
- **user-toolhive** aggregates multiple MCP servers (flux-operator, Grafana, Home Assistant, etc.). Always check which tools it exposes for the current task; use its Flux tools for reconcile/state when relevant, and other tools (e.g. Grafana, HA) when the task calls for them.
