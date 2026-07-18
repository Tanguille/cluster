# OpenCode VM Server Route Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the VM OpenCode server available to the in-cluster OpenCode web UI through an internal HTTPS Gateway hostname.

**Architecture:** The existing OpenCode HelmRelease remains the browser UI and local server. A selectorless Service plus static EndpointSlice represents the VM at `192.168.0.181:4096`; an internal HTTPRoute exposes it at `opencode-vm.${SECRET_DOMAIN}`. The VM permits the existing UI hostname through CORS and authenticates clients locally.

**Tech Stack:** Flux, Kustomize, Gateway API, Envoy Gateway, Kubernetes Service and EndpointSlice, OpenCode.

---

### Task 1: Remove the obsolete LM Studio provider

**Files:**
- Modify: `kubernetes/apps/ai/opencode/app/config/opencode.jsonc:41-58`

- [ ] **Step 1: Treat manifest rendering as the regression check**

There is no application unit-test harness for this JSONC configuration. The repository's Helm/Kustomize render validation is the focused regression check.

- [ ] **Step 2: Remove only the `lmstudio` provider object**

Leave the `litellm` provider and its closing braces intact so the provider section is:

```jsonc
  "provider": {
    "litellm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "LiteLLM",
      "options": {
        "baseURL": "http://litellm.ai.svc.cluster.local/v1",
        "apiKey": "{env:LITELLM_API_KEY}"
      }
    }
  }
```

### Task 2: Represent the VM as a Kubernetes service

**Files:**
- Create: `kubernetes/apps/ai/opencode/app/opencode-vm-service.yaml`
- Create: `kubernetes/apps/ai/opencode/app/opencode-vm-endpointslice.yaml`
- Modify: `kubernetes/apps/ai/opencode/app/kustomization.yaml:5-7`

- [ ] **Step 1: Create the selectorless headless Service**

```yaml
---
apiVersion: v1
kind: Service
metadata:
  name: opencode-vm
spec:
  type: ClusterIP
  clusterIP: None
  ports:
    - name: http
      port: 4096
      targetPort: 4096
      protocol: TCP
```

- [ ] **Step 2: Create the static EndpointSlice**

```yaml
---
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: opencode-vm
  labels:
    kubernetes.io/service-name: opencode-vm
addressType: IPv4
endpoints:
  - addresses:
      - 192.168.0.181
ports:
  - name: http
    port: 4096
    protocol: TCP
```

- [ ] **Step 3: Include both resources in the app Kustomization**

```yaml
resources:
  - helmrelease.yaml
  - opencode-vm-service.yaml
  - opencode-vm-endpointslice.yaml
  - opencode-vm-httproute.yaml
```

### Task 3: Add the internal HTTPS route for the VM

**Files:**
- Create: `kubernetes/apps/ai/opencode/app/opencode-vm-httproute.yaml`

- [ ] **Step 1: Create the HTTPRoute**

```yaml
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: opencode-vm
spec:
  hostnames:
    - "opencode-vm.${SECRET_DOMAIN}"
  parentRefs:
    - name: envoy-internal
      namespace: network
  rules:
    - backendRefs:
        - name: opencode-vm
          port: 4096
```

### Task 4: Configure and verify the VM server

**Files:**
- No repository file; VM-local OpenCode configuration only.

- [ ] **Step 1: Permit the browser UI origin in the VM configuration**

Set the VM OpenCode server configuration to:

```json
{
  "server": {
    "cors": ["https://opencode.${SECRET_DOMAIN}"]
  }
}
```

- [ ] **Step 2: Ensure the VM server is reachable and authenticated**

Run OpenCode on the VM with a non-empty `OPENCODE_SERVER_PASSWORD`, bound to `0.0.0.0:4096`. Do not store its password in Git.

- [ ] **Step 3: Validate manifests**

Run:

```bash
bash .agents/skills/pr-review/scripts/validate-pr.sh
```

Expected: the changed Kustomization and HelmRelease render without errors.

- [ ] **Step 4: Validate the reconciled route**

After Flux reconciliation, confirm the new HTTPRoute reports `Accepted=True` and `ResolvedRefs=True`, then add this URL in the web UI:

```text
https://opencode-vm.${SECRET_DOMAIN}
```

Expected: the server is added and a session can open with the VM credentials.
