# OpenCode VM server route

## Goal

Allow the OpenCode web UI at `https://opencode.${SECRET_DOMAIN}` to add the
OpenCode server running on `192.168.0.181:4096` without browser mixed-content
or CORS failures.

## Decision

Keep the existing in-cluster OpenCode deployment. Add a second, internal-only
Gateway route for the VM-backed server at
`https://opencode-vm.${SECRET_DOMAIN}`.

The `ai/opencode` Kustomization will create a selectorless `Service` and a
static `EndpointSlice` for `192.168.0.181:4096`, then route the new hostname
through `network/envoy-internal`. The existing OpenCode `HelmRelease`, service,
route, persistence, and LiteLLM provider remain unchanged. The obsolete LM
Studio provider is removed from the OpenCode config.

## VM requirements

The VM runs OpenCode on `0.0.0.0:4096`. Its configuration allows the web UI
origin in `server.cors`:

```json
{
  "server": {
    "cors": ["https://opencode.${SECRET_DOMAIN}"]
  }
}
```

The VM must use OpenCode password authentication and allow inbound TCP/4096
from the cluster's Envoy workload network. Password material stays on the VM
and is not added to this repository.

## User flow

1. The browser loads the in-cluster web UI over HTTPS.
2. The user adds `https://opencode-vm.${SECRET_DOMAIN}` as a server.
3. Envoy terminates TLS and proxies HTTP to the VM endpoint.
4. The VM permits the browser origin with CORS and validates the OpenCode
   credentials supplied in the server dialog.

## Validation

- Render the changed manifests with the repository validation script.
- Confirm Flux reports the route as accepted and its references resolved.
- Confirm `https://opencode-vm.${SECRET_DOMAIN}/global/health` reaches the VM.
- Add the HTTPS VM hostname in the OpenCode UI and confirm a session opens.

## Non-goals

- Broad LAN access from OpenCode.
- Replacing or deleting the existing in-cluster OpenCode server.
- Storing VM credentials in Git.
