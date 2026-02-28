# OpenCode on Kubernetes

We use the **bjw-s app-template** Helm chart. No dedicated OpenCode chart exists; the only public reference is [pilinux/opencode-docker](https://github.com/pilinux/opencode-docker) (Docker image + Compose).

**Layout:**

- **Persistence**: PVC at `/home/opencode/.config/opencode` (VolSync).
- **Config**: ConfigMap `opencode-config` mounted as `opencode.jsonc` (init copies to PVC). Reloader restarts the deployment when the ConfigMap changes.
- **Env**: `HOME=/home/opencode`; secrets via `opencode-secret`.
- **MCP**: URL is set via **`OPENCODE_MCP_URL`** in `opencode-secret`; the config uses `{env:OPENCODE_MCP_URL}`. Use your **public** MCP URL (e.g. `https://mcp.<your-domain>/mcp`) because OpenCode Web connects from the **browser**, which cannot reach in-cluster hostnames.
- **Ollama**: In-cluster URL `http://ollama.ai.svc.cluster.local:11434/v1` (used by the OpenCode server in the pod).

**Why we use an env var for the MCP URL:** Flux `substituteFrom` only reads secrets in the **same namespace** as the Kustomization. The OpenCode Kustomization is in `ai` while `cluster-secrets` (with `SECRET_DOMAIN`) lives in `flux-system`, so `${SECRET_DOMAIN}` was never substituted in the ConfigMap and the pod saw an invalid URL. Storing the full MCP URL in `opencode-secret` as `OPENCODE_MCP_URL` avoids that and keeps the config correct.

Config is in Git (`app/config/opencode.jsonc`). The secret is SOPS-encrypted (`app/secret.sops.yaml`).

**One-time: set the MCP URL in the secret**

The repo ships with `OPENCODE_MCP_URL: REPLACE_WITH_YOUR_PUBLIC_MCP_URL`. Set your real URL (e.g. `https://mcp.<your-domain>/mcp`) so OpenCode can reach the MCP gateway:

```bash
sops kubernetes/apps/ai/opencode/app/secret.sops.yaml
# Set OPENCODE_MCP_URL to your public MCP URL, save and exit. Then commit and push; Flux will apply the updated secret.
```

Then force a rollout so the pod picks up the new env: `kubectl rollout restart deployment/opencode -n ai`.

**If MCP still shows "Unable to connect":**

1. **Check the config and env in the pod** – config should reference `{env:OPENCODE_MCP_URL}` and the secret must set that env:

   ```bash
   kubectl exec -n ai deployment/opencode -- cat /home/opencode/.config/opencode/opencode.jsonc | grep -o '"url":[^,]*'
   kubectl exec -n ai deployment/opencode -- env | grep OPENCODE_MCP_URL
   ```

2. **From your browser’s machine**, ensure the MCP host is reachable: `curl -sI https://mcp.<your-domain>/mcp`.

3. **From inside the pod** (in case the server connects, not the browser):
   `kubectl exec -n ai deployment/opencode -- wget -qO- --post-data='{"jsonrpc":"2.0","method":"tools/list","id":1}' --header='Content-Type: application/json' https://mcp.<your-domain>/mcp 2>&1 | head -5`

---

## Findings from GitHub (why “Unable to connect” may persist)

Searches in [anomalyco/opencode](https://github.com/anomalyco/opencode) and [pilinux/opencode-docker](https://github.com/pilinux/opencode-docker) point to several causes that can apply when OpenCode is deployed (e.g. Kubernetes / reverse proxy) and MCP still fails:

1. **Who connects to the remote MCP URL**
   Docs say the Web UI connects from the **browser** for remote MCP, so the URL must be reachable from the client. In some setups the connection may actually be made by the **OpenCode server** (the pod). If so, the pod must be able to reach `https://mcp.<your-domain>/mcp` (DNS, egress, TLS). **Check both:** from your machine `curl -sI https://mcp.<domain>/mcp` and from inside the pod `kubectl exec -n ai deployment/opencode -- wget -qO- --post-data='{"jsonrpc":"2.0","method":"tools/list","id":1}' --header='Content-Type: application/json' https://mcp.<your-domain>/mcp 2>&1 | head -5`.

2. **OpenCode version regressions**
   [Issue #8171](https://github.com/anomalyco/opencode/issues/8171): MCP (including remote) broke for many users after upgrading from 1.1.6 to 1.1.7+ (connection fails or tools unavailable, no clear error). [Issue #8434](https://github.com/anomalyco/opencode/issues/8434): local MCP stdin closes immediately after spawn; workaround reported is running the native Bun binary directly. If you recently upgraded the image, try pinning to a known-good tag (e.g. the version used in [pilinux/opencode-docker](https://github.com/pilinux/opencode-docker)) and compare.

3. **Remote MCP transport (SSE / HTTP)**
   [Issue #834](https://github.com/sst/opencode/issues/834) / [#8406](https://github.com/anomalyco/opencode/issues/8406): remote MCP servers using Server-Sent Events (SSE) can fail (wrong `Accept` headers, connection hangs). If your MCP gateway (e.g. toolhive/vmcp) uses SSE, the gateway must speak the format OpenCode expects, or you may need a different transport.

4. **Config substitution (addressed in this repo)**
   We avoid Flux substitution by storing the MCP URL in `opencode-secret` as `OPENCODE_MCP_URL` and using `{env:OPENCODE_MCP_URL}` in the config. You must set that value once with `sops` (see “One-time: set the MCP URL” above).

5. **MCP OAuth in remote/server setups**
   [Issue #7887](https://github.com/anomalyco/opencode/issues/7887), [#8274](https://github.com/anomalyco/opencode/issues/8274), [#9081](https://github.com/anomalyco/opencode/issues/9081): when OpenCode runs on a remote host or in a container, OAuth callbacks (e.g. `http://127.0.0.1:19876/mcp/oauth/callback`) are unreachable from the user’s browser, so MCPs that require OAuth (e.g. Sentry, GitHub) can fail. Workarounds: SSH port forward (`ssh -L 1455:127.0.0.1:1455 user@host` for auth callback), or use headless/device-code flows where implemented. This applies to MCPs that need OAuth, not necessarily to a simple HTTP/SSE toolhive gateway.

**References (selection):**

- [anomalyco/opencode#8682](https://github.com/anomalyco/opencode/issues/8682) – MCP “can’t use” (duplicate suggestions point to #5444, #8171, #8406).
- [anomalyco/opencode#8171](https://github.com/anomalyco/opencode/issues/8171) – MCP fails after 1.1.6→1.1.7+.
- [anomalyco/opencode#8406](https://github.com/anomalyco/opencode/issues/8406) – Connection hangs with non-standard SSE/HTTP MCP servers.
- [anomalyco/opencode#9081](https://github.com/anomalyco/opencode/issues/9081) – WSL2/devcontainer: MCP OAuth callback unreachable (callback bind host).
- [pilinux/opencode-docker](https://github.com/pilinux/opencode-docker) – Docker/Compose reference; no K8s; config via `opencode.json` and volumes.
