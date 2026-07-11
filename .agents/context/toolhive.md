# ToolHive and Moltis Context

**When to use:** ToolHive, MCPServer, VirtualMCPServer, optimizer, observability, MCP endpoint, Moltis, or MCP networking.

- MCPServer secret-backed environment variables use `spec.secrets` with `targetEnvName`; `env[].valueFrom` is not supported by the CRD.
- MCPServer transport is `streamable-http`; `streamablehttp` is invalid.
- Keep `*-opt` MCPServer objects in the same file as the primary object and fully duplicate `spec`; YAML anchors do not resolve across `---` documents.
- Treat `kubernetes/apps/ai/toolhive/app/ocirepository.yaml` and `kubernetes/apps/ai/toolhive/crds/ocirepository.yaml` as the source of truth for pinned versions. Use `.agents/skills/toolhive-upgrades/references/breaking-history.md` for migration audits.
- VirtualMCPServer and MCPServer must not share a name in one namespace because both create a Deployment with that name.
- `user-toolhive` exposes the `flux`, `observability`, `homeassistant`, `resources`, `search`, and `database` groups. Prefer these MCP tools over raw kubectl for their domains; use `flux` by default and `database` for database work. Tool names are group-prefixed.
- The observability MCP server contains Grafana, Prometheus, and read-only Talos diagnostics. If unavailable in a session, use the Alertmanager API or kubectl.
- Public `mcp-*.${SECRET_DOMAIN}` routes target VirtualMCPServer backends. The optimizer endpoint is `mcp-unified.${SECRET_DOMAIN}` to `vmcp-unified` in namespace `ai`, port `4483`, path `/mcp`.
- Inside the cluster, use `vmcp-*` Services in namespace `ai`, port `4483`, path `/mcp`; do not use the `mcp-*-proxy` Services on port `8080`.
- Moltis uses Docker-in-Docker. Configure dockerd DNS toward cluster/CoreDNS because dockerd, not the pod `dnsConfig`, resolves names inside tool and browser sandboxes.
- Moltis spawns stdio MCP servers from the gateway image. If that image lacks Node or `npx`, use a custom gateway image or run the MCP server over HTTP/SSE.
