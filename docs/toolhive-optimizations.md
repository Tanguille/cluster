# ToolHive optimizations (from official docs)

Improvements identified from [ToolHive documentation](https://docs.stacklok.com/toolhive/) that are relevant to this repo. Grouping by subdomain is already in place; below are additional options.

## 1. Tool filtering (reduce token surface)

**Docs:** [Tool aggregation](https://docs.stacklok.com/toolhive/guides-vmcp/tool-aggregation), [Customize tools](https://docs.stacklok.com/toolhive/guides-k8s/customize-tools)

- **VirtualMCPServer:** For groups with multiple backends (e.g. `resources`: github, sequentialthinking, karakeep), you can add a `tools` filter per workload so only needed tools are exposed:

  ```yaml
  spec:
    config:
      aggregation:
        tools:
          - workload: github
            filter: ['list_issues', 'get_issue', 'create_issue']  # only what you use
  ```

- **MCPToolConfig:** Servers already reference `MCPToolConfig`; most have `spec: {}`. Add `toolsFilter` to expose only the tools you use, and optionally `toolsOverride` with shorter `description` values to cut token usage. **No regex/rewrite:** The CRD only supports a per-tool map (`toolsOverride`); there is no strip-prefix or pattern. For Home Assistant we use **`scripts/generate-toolhive-ha-overrides.sh`** to apply the "strip `ha_`" rule: it fetches ha-mcp source, extracts all `ha_*` tool names, and emits the full `toolsOverride` YAML. Run when upgrading ha-mcp (`./scripts/generate-toolhive-ha-overrides.sh yaml` then merge the output into `config/homeassistant.yaml`). **60-char limit:** Combined server + tool name must be ≤60 characters or tools are filtered out; shortening/prefix-stripping helps (e.g. Grafana long names, Home Assistant `ha_` strip).

**Worth it when:** You have many tools per server and only use a subset; or tool descriptions are long and you want to shorten them.

---

## 2. Flux operator options (not used here)

**Docs:** [Flux Operator MCP config](https://fluxoperator.dev/docs/mcp/config/), [K8s MCP guide](https://docs.stacklok.com/toolhive/guides-mcp/k8s)

- **`--read-only`:** Disables reconcile/suspend/resume/apply/delete. We do **not** use this; the flux-operator MCP is intended to allow reconcile/write from the client.
- **`--mask-secrets`:** Default is true; keeps secret values masked in responses. Not necessarily required for our setup; leave default or set `--mask-secrets=false` if you need unmasked values.

---

## 3. Authentication (production)

**Docs:** [Auth on Kubernetes](https://docs.stacklok.com/toolhive/guides-k8s/auth-k8s)

All VirtualMCPServers use `incomingAuth: type: anonymous`. For anything beyond internal/trusted use:

- Use HTTPS (already via gateway).
- Prefer external IdP (Google, GitHub, Azure AD, etc.) or shared OIDC via ConfigMap.
- Optionally service-to-service auth with Kubernetes service account tokens.

No code change suggested here; document when you expose MCP beyond the internal network.

---

## 4. MCP Optimizer (future / experimental)

**Docs:** [Reduce token usage with MCP Optimizer](https://docs.stacklok.com/toolhive/tutorials/mcp-optimizer)

- Uses `find_tool` (semantic + keyword search) and `call_tool` so the model sees only a small set of relevant tools (e.g. 8) instead of the full list.
- Reported savings: e.g. 68–85% token reduction on some workloads.
- **Status:** Experimental; documented for ToolHive UI/CLI (e.g. “Linux: Not currently supported”). No Kubernetes/operator CRD found in this repo.
- **Action:** Revisit when ToolHive documents K8s/operator support; then you can put Optimizer in front of a group (or per subdomain) for additional token savings without changing group layout.

---

## 5. Resource and operational notes

- **MCPServer resources:** Current requests/limits are small and reasonable; adjust if you see OOMKills or CPU throttling.
- **Single-backend groups:** flux, observability, homeassistant, search each have one MCPServer. Keeping `conflictResolution: prefix` is fine and future-proof if you add more servers to a group later.

---

## Troubleshooting: Home Assistant MCP

**Deployment name collision (fixed):** The VirtualMCPServer for the homeassistant group was named `homeassistant`, same as the MCPServer. The operator creates a Deployment with the same name as each resource, so both tried to own Deployment `homeassistant` → "selector does not match template labels". The VirtualMCPServer was renamed to `ha` so its Deployment is `ha` and service `vmcp-ha`; the HTTPRoute was updated to use `vmcp-ha`.

**If it still fails (cluster is Ready):** Check client side: (1) In Cursor → Installed MCP Servers, ensure **homeassistant is enabled** (toggle on). It may have been auto-disabled after earlier 500s. (2) Client URL must be `https://mcp-homeassistant.<your-domain>/mcp`. (3) Restart Cursor or reconnect MCP after enabling.

If the homeassistant MCP endpoint still fails after the above:

1. **Find the right pods** (operator uses a proxy; no pod has label `app.kubernetes.io/name=homeassistant`)

   ```bash
   kubectl -n ai get pods | grep -E 'homeassistant|vmcp'
   ```

   - **Proxy pod** (runs/wraps ha-mcp): name like `mcp-homeassistant-proxy-xxxxx`
   - **VirtualMCPServer pod** (gateway the client hits): name like `vmcp-homeassistant-xxxxx`

2. **Logs that will show the real error**

   ```bash
   # Proxy (stdio → streamable-http): where ha-mcp runs or is attached
   kubectl -n ai logs deployment/mcp-homeassistant-proxy --all-containers --tail=150

   # If 500 is from the gateway side, check vMCP pod
   kubectl -n ai get pods -l app.kubernetes.io/name=vmcp-homeassistant -o name
   kubectl -n ai logs <vmcp-homeassistant-pod> --tail=150
   ```

3. **Verify env in the proxy’s MCP container** (optional)

   ```bash
   POD=$(kubectl -n ai get pods -l app.kubernetes.io/name=mcp-homeassistant-proxy -o jsonpath='{.items[0].metadata.name}')
   kubectl -n ai exec "$POD" -c mcp -- env | grep -E 'HOMEASSISTANT_URL|HOMEASSISTANT_TOKEN'
   ```

4. **MCPServer status**

   ```bash
   kubectl -n ai describe mcpserver homeassistant
   ```

   Status.URL should be `http://mcp-homeassistant-proxy.ai.svc.cluster.local:8080/mcp` when running.

---

## Summary

| Improvement              | Effort  | When to consider                          |
|--------------------------|---------|-------------------------------------------|
| Tool filtering (vMCP/MCPToolConfig) | Low–med | When you want fewer/short tool descriptions |
| Flux `--read-only`       | N/A    | Not used; we keep write/reconcile enabled  |
| Auth (non-anonymous)     | Medium  | When exposing MCP beyond internal use     |
| MCP Optimizer            | N/A now | When ToolHive adds K8s/operator support   |
