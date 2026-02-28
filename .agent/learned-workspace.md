# Learned Workspace Facts

**When to use:** HTTPRoute, ToolHive, MCPServer, Flux substituteFrom, cluster-secrets, media apps, Talos, Reloader, in-cluster URL, cnpg status, flux-operator, continual-learning.

Stable, non-sensitive facts about this cluster and tooling.

- For continual-learning (or mining transcripts): update `.agent/learned-preferences.md` and `.agent/learned-workspace.md` only; do not add learned sections to AGENTS.md (it stays short and points to .agent/ for on-demand context).

- Use in-cluster service URLs (e.g. `http://service.namespace.svc.cluster.local:port`) for pod-to-pod calls (e.g. OpenCode → MCP gateway, OpenCode → Ollama) to avoid external DNS or hairpinning.
- Annotate deployments with Reloader (e.g. `reloader.stakater.com/auto: "true"`) so pods restart when referenced ConfigMaps change.
- Follow KISS principles; avoid init containers or extra complexity unless needed (e.g. config not seen due to mount order).
- Internal HTTPRoutes must use parentRef name `envoy-internal` (not `internal`); k8s-gateway serves DNS for routes attached to that gateway.
- ToolHive MCPServer: use `spec.secrets` with `targetEnvName` for secret-backed env vars; `env[].valueFrom` is not supported by the CRD.
- ToolHive MCPServer transport must be `streamable-http` (with hyphen); `streamablehttp` is invalid per CRD.
- Talos kernel args (e.g. `talos.dashboard.disabled=1`) go in schematic.yaml; existing nodes need schematic rebuild and Talos upgrade to pick them up.
- **user-toolhive** exposes MCP servers **flux**, **observability**, **homeassistant**, **resources**, **search** (subdomains `mcp-flux.${SECRET_DOMAIN}` etc.). Prefer these MCP tools over raw kubectl for reconcile, logs, Grafana, HA, GitHub, search. For this repo use **flux**. Tool names are prefixed by group (e.g. `flux-operator_reconcile_flux_kustomization`).
- For one-shot privileged pods (e.g. disk zap), use a YAML manifest and `kubectl apply -f`; `kubectl run --overrides` with complex JSON often fails with "Invalid JSON Patch".
- When re-running the same one-shot pod (e.g. zap), delete the pod first (`kubectl delete pod <name> --ignore-not-found`) then apply; pod spec is largely immutable.
- CloudNative-PG postgres16 and barman-cloud plugin run in namespace `database`; the plugin Deployment is named `barman-cloud-plugin-barman-cloud` (Service is `barman-cloud`). To re-add a missing instance after its join job was deleted, delete that instance's PVC then force-reconcile so the operator creates a new PVC and join job.
