# Learned Workspace Facts

**When to use:** HTTPRoute, ToolHive, MCPServer, Flux substituteFrom, cluster-secrets, media apps, Talos, Reloader, in-cluster URL, cnpg status, flux-operator, continual-learning.

Stable, non-sensitive facts about this cluster and tooling.

- For continual-learning: update `.agents/learned-preferences.md` and `.agents/learned-workspace.md`; also keep the short `## Learned User Preferences` / `## Learned Workspace Facts` blocks in `AGENTS.md` in sync (plain bullets only, max 12 per section); full detail stays in `.agents/`.

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
- ToolHive: VirtualMCPServer and MCPServer must not share the same name in the same namespace (both create a Deployment with that name; use e.g. VirtualMCPServer `ha` when MCPServer is `homeassistant` to avoid collision).
- With ceph-block (RWO) storage, use Deployment strategy Recreate; RollingUpdate is not supported.
- app-template chart default deployment strategy is Recreate; if a chart still emits `rollingUpdate` alongside `Recreate`, Kubernetes rejects the Deployment—fix with upstream chart, postRenderer, or a patch.
- The Cursor MCP server for Grafana/Prometheus alerts is named **observability** (see `~/.cursor/mcp.json`). A given agent session may only register a subset of MCP servers; use Alertmanager API or kubectl when `call_mcp_tool` for observability or flux is unavailable.
- Flux `postBuild.substituteFrom` replaces `${...}` in rendered manifests; escape literals the shell must see (Flux/Kustomize `$$` patterns) or hardcode IDs, or Flux will empty unintended matches.
- Ceph `mon_data_avail_warn` defaults to 30%; low EPHEMERAL free **percentage** on a Talos mon node can trigger MON_DISK_LOW even with large absolute free space—address node disk headroom first; lowering the threshold is a last resort, not the primary fix.

## Recyclarr Config

- `select_all: true` imports ALL custom formats from Trash Guides regardless of default status
- `delete_old_custom_formats` is deprecated in v8.4.0 - auto-replacement is now default
- Custom format YAML files often have duplicate trash_ids (with and without scores) - can be cleaned up
- Dual Audio = Japanese + English, not Japanese only
