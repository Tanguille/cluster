# ToolHive MCP servers

## Current backends (mcp-tools group)

| Server | Transport | Status / notes |
|--------|-----------|----------------|
| flux-operator | stdio | Uses kubeconfig from toolhive-secrets. |
| grafana | stdio | GRAFANA_URL set to internal service (grafana-service.observability:3000). Optional: **GRAFANA_SERVICE_ACCOUNT_TOKEN** in toolhive-secrets if Grafana API requires auth (see Grafana setup below). |
| github | stdio | Official [GitHub MCP Server](https://github.com/github/github-mcp-server). Needs GITHUB_PERSONAL_ACCESS_TOKEN in toolhive-secrets. |
| homeassistant | streamable-http | [ha-mcp](https://github.com/homeassistant-ai/ha-mcp) in HTTP mode (`ha-mcp-web`). Long-lived server; no proxy loop. Nothing installed in HA. Needs HOMEASSISTANT_API_TOKEN in toolhive-secrets (as HOMEASSISTANT_TOKEN). |
| karakeep | stdio | KARAKEEP_API_ADDR points at karakeep.default:3000; KARAKEEP_API_KEY in toolhive-secrets. |
| searxng | stdio | SEARXNG_URL is cluster-internal (e.g. `http://searxng.default.svc.cluster.local:8080`). SearXNG limiter has `pass_ip` for cluster IPs so the ToolHive pod is not blocked (403). |
| sequentialthinking | stdio | No extra config. |
| *(zigbee2mqtt removed)* | — | Use **Home Assistant MCP** to control Zigbee devices when Zigbee2MQTT is integrated with HA. |

## Home Assistant (mcp-proxy) and pod readiness

The Home Assistant backend uses [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) in “stdio → Streamable HTTP” client mode. That mode exits when the session ends, so we run a **restart loop** in `config/homeassistant.yaml` (no sleep) so the next gateway request usually hits a live process; ToolHive’s proxy expects stdio to the container, so server mode (mcp-proxy listening on 8080) caused protocol errors (e.g. gateway “wrote more than Content-Length”). Alternative: [MCPRemoteProxy](https://docs.stacklok.com/toolhive/guides-k8s/remote-mcp-proxy) with `transport: streamable-http` and `headerForward.addHeadersFromSecret` for the HA token, but it **requires OIDC** for incoming client auth.

### Home Assistant: GitHub and known issues

- **ToolHive streamable-http:** [ToolHive issue #54](https://github.com/stacklok/toolhive/issues/54) tracks support for the new MCP Streaming HTTP transport. The gateway may be tuned for that; MCPServer + mcp-proxy (stdio bridge) can still hit 502 or "connection refused" when the proxy's health check hits the restart window or when the gateway sees a malformed response (e.g. superfluous response.WriteHeader / "wrote more than Content-Length" from mcp-go).
- **mcp-proxy:** In [sparfenyuk/mcp-proxy](https://github.com/sparfenyuk/mcp-proxy), client mode (stdio → URL) is intended to be started by the client and **exits when the session ends** ([README](https://github.com/sparfenyuk/mcp-proxy#about)); there is no long-lived server mode that fits ToolHive's stdio expectation. Streamable HTTP support was added in [issue #59](https://github.com/sparfenyuk/mcp-proxy/issues/59).
- **If you have OIDC:** Use [MCPRemoteProxy](https://docs.stacklok.com/toolhive/guides-k8s/remote-mcp-proxy) with `remoteURL: https://homeassistant.${SECRET_DOMAIN}/api/mcp`, `transport: streamable-http`, and `headerForward.addHeadersFromSecret` with `headerName: Authorization` and the secret value `Bearer <your-long-lived-token>`. That avoids the stdio bridge and 502s; the proxy forwards Streamable HTTP directly to HA.

### How to fix the Home Assistant backend (502 / unhealthy)

1. **Replicas (current setup)**
   The Home Assistant MCPServer is set to `replicas: 2`. The gateway round-robins; when one pod’s mcp-proxy is restarting, the other may be ready. After pushing, run `./scripts/debug-toolhive-mcp.sh` and check `discoveredBackends` for homeassistant. If the operator ignores `replicas`, remove it.

2. **Use MCPRemoteProxy instead (no stdio bridge)**
   If you have an OIDC IdP (Keycloak, Dex, cloud IdP): create an `MCPRemoteProxy` with `remoteURL: https://homeassistant.${SECRET_DOMAIN}/api/mcp`, `transport: streamable-http`, and `headerForward.addHeadersFromSecret` for `Authorization: Bearer <HA long-lived token>`. Register it in the same group or expose it so the gateway can use it. Then remove or disable the Home Assistant MCPServer so the gateway only uses the remote proxy. See [Proxy remote MCP servers](https://docs.stacklok.com/toolhive/guides-k8s/remote-mcp-proxy).

3. **Use Home Assistant from Cursor only (no ToolHive)**
   For Cursor, you can skip the gateway and point Cursor’s MCP at HA via mcp-proxy locally ([HA docs – Cursor](https://www.home-assistant.io/integrations/mcp_server/#example-cursor)). ToolHive’s aggregated tools won’t include HA in that case.

### Home Assistant backend: ha-mcp (current)

We use **[ha-mcp](https://github.com/homeassistant-ai/ha-mcp)** as the MCP server in the cluster: long-lived Streamable HTTP, 97 tools, no proxy loop. **Nothing is installed in Home Assistant**; HA is the API backend. Same long-lived token in `toolhive-secrets` as `HOMEASSISTANT_API_TOKEN`, mapped to `HOMEASSISTANT_TOKEN`; `HOMEASSISTANT_URL` is set to `https://homeassistant.${SECRET_DOMAIN}`.

If the homeassistant pod shows **ImagePullBackOff** (image `ghcr.io/homeassistant-ai/ha-mcp` not found), build from the [ha-mcp Dockerfile](https://github.com/homeassistant-ai/ha-mcp/blob/master/Dockerfile) and push to your registry, then set `spec.image` in `config/homeassistant.yaml` to your image. The container must support `command: ["ha-mcp-web"]` and listen on port 8086.

1. **Upstream**
   If 502s continue: open an issue on [ToolHive](https://github.com/stacklok/toolhive/issues) (streamable-http backend with stdio bridge that exits per session) and/or [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy/issues) (long-lived mode or compatibility with “restart after each session” sidecars).

## Zigbee devices

Zigbee devices are controlled via the **Home Assistant MCP** when Zigbee2MQTT (or another Zigbee integration) is set up in Home Assistant. No separate zigbee2mqtt MCP server is deployed.

## Grafana setup

The Grafana MCP image (`mcp/grafana`) defaults to **SSE transport** (HTTP on port 8000). For ToolHive’s stdio proxy we pass **args: ["-t", "stdio"]** in the MCPServer spec so the server runs in stdio mode.

The Grafana MCP server needs a **service account token** to call the Grafana API (unless Grafana allows anonymous access). Without it, tools may not appear and the backend may show unhealthy.

1. In Grafana: **Administration → Service accounts → Add service account** (e.g. name “mcp-toolhive”). Create a token and copy it.
2. Add it to toolhive-secrets: `sops kubernetes/apps/ai/toolhive/app/secret.sops.yaml` and add a key `GRAFANA_SERVICE_ACCOUNT_TOKEN` with the token value (under `stringData`).
3. Commit and push; after reconciliation the Grafana MCP pod will get the token and tools should appear.

Alternative: you can use **GRAFANA_USERNAME** and **GRAFANA_PASSWORD** (e.g. admin) instead by adding both keys to the secret and referencing them in the MCPServer spec (secrets with targetEnvName). The config above uses the service account token only.

## GitHub setup

Add **GITHUB_PERSONAL_ACCESS_TOKEN** to `toolhive-secrets` (edit with `sops kubernetes/apps/ai/toolhive/app/secret.sops.yaml`). Create a [GitHub PAT](https://github.com/settings/personal-access-tokens/new) with the scopes your AI use case needs (e.g. repo, read:org). Optional: for GitHub Enterprise set env `GITHUB_HOST` in the MCPServer (e.g. `https://octocorp.ghe.com`).

## Debugging

**1. Gateway and backend status**

```bash
kubectl get virtualmcpserver tools-gateway -n ai -o yaml
# Check status.discoveredBackends for each server (healthy / unhealthy / unknown).
```

**2. List MCP-related pods**

ToolHive creates deployments/statefulsets named after the MCPServer (e.g. `grafana`, `homeassistant`, `searxng`). Services are `mcp-<name>-proxy`. List pods:

```bash
kubectl get pods -n ai | grep -E 'mcp-|homeassistant|grafana|flux|karakeep|searxng|sequential|tools-gateway'
```

**3. Pod not Running (CreateContainerConfigError / ImagePullBackOff)**

- **Grafana:** If `discoveredBackends` shows grafana as **"Health check timed out"**, the proxy is reachable but the MCP process does not respond in time (often it blocks on the Grafana API). Run `./scripts/debug-toolhive-mcp.sh` and check the Grafana proxy logs (`deployment/grafana`); verify `GRAFANA_URL` is correct and Grafana is reachable from the ai namespace. If using auth, add `GRAFANA_SERVICE_ACCOUNT_TOKEN` to toolhive-secrets.
- **Home Assistant:** Ensure `HOMEASSISTANT_API_TOKEN` exists in `toolhive-secrets`.
- **GitHub:** Ensure `GITHUB_PERSONAL_ACCESS_TOKEN` exists in `toolhive-secrets`.
- **SearXNG:** Ensure `SEARXNG_URL` includes the correct port (SearXNG service in `default` uses port **8080**). Use `http://searxng.default.svc.cluster.local:8080`.
- Check: `kubectl describe pod -n ai <pod-name>` for the exact error.

**4. Logs**

From the repo root, collect status and logs for HA and Zigbee2MQTT in one go (then paste when debugging):

```bash
./scripts/debug-toolhive-mcp.sh
```

Or manually:

```bash
# vMCP gateway (aggregator)
kubectl logs -n ai deployment/tools-gateway -f

# Per-server proxy (deployments: grafana, homeassistant, searxng)
kubectl logs -n ai deployment/grafana -c mcp -f
# Or by app label:
kubectl logs -n ai -l app.kubernetes.io/name=homeassistant -c mcp --tail=100
kubectl logs -n ai -l app.kubernetes.io/name=homeassistant -c mcp --tail=100
```

**5. Home Assistant 405 / connection**

- mcp-proxy args must have the **URL as first argument**, then `--transport=streamablehttp`, then `--stateless`.
- HA must be reachable from the cluster at `https://homeassistant.${SECRET_DOMAIN}/api/mcp`; token must be valid.

## Cleanup old pods and ReplicaSets

When you change an MCPServer spec (e.g. args, env), the ToolHive operator updates the proxy **Deployment**. Kubernetes does a rolling update and keeps previous **ReplicaSets** for rollback (revision history). So you can end up with:

- **Old proxy pods** (CrashLoopBackOff or Completed) from a previous ReplicaSet
- **Old ReplicaSets** with 0 desired replicas
- **StatefulSet pod** (e.g. `homeassistant-0`) plus **Deployment pods** (e.g. `homeassistant-<hash>-<suffix>`) — one Deployment is “current”, the rest are from old revisions

The operator does not delete these; you can clean them up manually:

```bash
# 1) See what you have
kubectl get deploy,rs,pods -n ai | grep -E 'homeassistant|grafana|flux|karakeep|searxng|sequential'

# 2) Delete failed/completed pods (they won’t be recreated if the ReplicaSet is scaled to 0)
kubectl delete pod -n ai --field-selector=status.phase=Failed
kubectl delete pod -n ai --field-selector=status.phase=Succeeded

# 3) Delete old ReplicaSets with 0 replicas (keeps revision history from growing)
kubectl get rs -n ai -o jsonpath='{range .items[?(@.spec.replicas==0)]}{.metadata.name}{"\n"}{end}' | xargs -r kubectl delete rs -n ai
```

**Operator gap:** The [ToolHive operator](https://github.com/stacklok/toolhive) does not set `revisionHistoryLimit` on the proxy Deployments it creates (repo search returns 0 hits), and there is no open issue for it. Consider opening an issue at <https://github.com/stacklok/toolhive/issues> with something like:

**Title:** Set revisionHistoryLimit on proxy Deployments to avoid accumulating old ReplicaSets
**Body:** When an MCPServer spec changes, the proxy Deployment is updated and Kubernetes keeps previous ReplicaSets for rollback. The operator does not set `revisionHistoryLimit` on the Deployment, so old ReplicaSets and their failed/completed pods accumulate. Please set `revisionHistoryLimit: 1` (or make it configurable) on the proxy Deployment so only the current revision is retained. Related: #3411 (StatefulSet readiness).
