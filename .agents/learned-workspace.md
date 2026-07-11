# Learned Workspace Facts

**When to use:** HTTPRoute, ToolHive, MCPServer, Flux substituteFrom, cluster-secrets, media apps, Talos, Reloader, in-cluster URL, CNPG, Rook, or continual learning.

Stable, non-sensitive facts about this cluster and tooling.

- For continual learning, update `.agents/learned-preferences.md` or this file; keep full detail in `.agents/` rather than `AGENTS.md`.
- Use in-cluster service URLs (e.g. `http://service.namespace.svc.cluster.local:port`) for pod-to-pod calls to avoid external DNS or hairpinning.
- Annotate deployments with Reloader (e.g. `reloader.stakater.com/auto: "true"`) so pods restart when referenced ConfigMaps change.
- Follow KISS principles; avoid init containers or extra complexity unless needed.
- Internal HTTPRoutes use parentRef name `envoy-internal`; k8s-gateway serves DNS for routes attached to that gateway.
- ToolHive MCPServer secret-backed environment variables use `spec.secrets` with `targetEnvName`; `env[].valueFrom` is unsupported.
- ToolHive MCPServer transport is `streamable-http`; `streamablehttp` is invalid.
- Keep `*-opt` MCPServer objects in the same file as the primary object and fully duplicate `spec`; YAML anchors do not resolve across `---` documents.
- For ToolHive versions, treat `kubernetes/apps/ai/toolhive/app/ocirepository.yaml` and `kubernetes/apps/ai/toolhive/crds/ocirepository.yaml` as authoritative. Use `.agents/skills/toolhive-upgrades/references/breaking-history.md` for migration audits.
- Talos kernel arguments belong in `schematic.yaml`; existing nodes need a schematic rebuild and Talos upgrade to receive them.
- `user-toolhive` exposes `flux`, `observability`, `homeassistant`, `resources`, `search`, and `database`. Prefer these MCP tools over raw kubectl for their domains; use `flux` by default and `database` for database work.
- For one-shot privileged pods, use a YAML manifest and `kubectl apply -f`; complex `kubectl run --overrides` JSON is unreliable. Delete an existing pod before reapplying because pod specs are largely immutable.
- CloudNativePG postgres16 and the barman-cloud plugin run in namespace `database`. The plugin Deployment is `barman-cloud-plugin-barman-cloud`; its Service is `barman-cloud`. To re-add an instance after its join job was deleted, delete its PVC and force-reconcile.
- VirtualMCPServer and MCPServer must not share a name in one namespace because both create a Deployment with that name.
- With `ceph-block` RWO storage, use Deployment strategy `Recreate`; RollingUpdate is unsupported.
- The app-template chart defaults to `Recreate`. If it emits `rollingUpdate` too, fix the chart, postRenderer, or patch.
- The `observability` MCP server provides Grafana, Prometheus, and read-only Talos diagnostics. If unavailable in a session, use the Alertmanager API or kubectl.
- Flux `postBuild.substituteFrom` replaces `${...}` in rendered manifests. Escape literals with Flux/Kustomize `$$` patterns, or Flux may empty unintended matches.
- Ceph `mon_data_avail_warn` defaults to 30%. Address Talos node EPHEMERAL headroom before lowering the threshold.
- Moltis uses Docker-in-Docker. Configure dockerd DNS toward cluster/CoreDNS because dockerd, not the pod `dnsConfig`, resolves names inside tool and browser sandboxes.
- Public `mcp-*.${SECRET_DOMAIN}` routes target VirtualMCPServer backends. The optimizer endpoint is `mcp-unified.${SECRET_DOMAIN}` to `vmcp-unified` in namespace `ai`, port `4483`, path `/mcp`. Inside the cluster, use `vmcp-*` Services on port `4483`, not `mcp-*-proxy` Services on `8080`.
- The `database` MCP group uses `vmcp-database.ai.svc`, port `4483`, path `/mcp`, and requires `POSTGRES_MCP_DATABASE_URI` in SOPS-managed `toolhive-secrets`. Use a primary URI for admin statistics and tuning; replicas skew replication and buffer reporting.
- Moltis spawns stdio MCP servers from the gateway image. If it lacks Node or `npx`, use a custom image or run the MCP server over HTTP/SSE.
- Large Talos `/var/lib/containerd` usage is often old image layers. Configure kubelet image GC thresholds to run sooner; confirm Talos apply mode before assuming a reboot is unnecessary.

## Recyclarr

- `select_all: true` imports all custom formats from Trash Guides.
- `delete_old_custom_formats` is deprecated in v8.4.0; automatic replacement is the default.
- Consolidate duplicate `trash_ids` in custom-format YAML.
- Dual Audio means Japanese and English, not Japanese only.
