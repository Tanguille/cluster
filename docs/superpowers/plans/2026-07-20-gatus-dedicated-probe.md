# Gatus Dedicated Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace CrowdSec-blocked external application probes with one isolated public Gatus health probe while retaining CrowdSec on every existing public application route.

**Architecture:** Add `envoy-external-probe`, a separate HTTPS Envoy Gateway on reserved address `192.168.0.9`, and route only `gatus-probe.${SECRET_DOMAIN}/health` to Gatus's existing health endpoint. Remove `envoy-external` from Gatus sidecar discovery and add the dedicated HTTPS endpoint to its static config. The new Gateway deliberately has no CrowdSec `SecurityPolicy`; the existing `envoy-external` policy remains untouched.

**Tech Stack:** FluxCD, Kubernetes Gateway API, Envoy Gateway 1.8.2, Cilium LB IPAM, app-template Gatus chart, Gatus configuration.

---

## Verification evidence path

The claim is that Gatus alerts on the dedicated public endpoint rather than treating intentional CrowdSec blocks as application downtime, while ordinary public application traffic stays protected. The meaningful failure modes are: the route attaches to the CrowdSec-protected Gateway by mistake; Gatus still discovers application routes; DNS/TLS does not reach the probe Gateway; or the new address has not been reserved.

Before live rollout, establish structure with rendered manifests: `envoy-external-probe` alone owns `${ENVOY_GATUS_PROBE_IP}`, has no matching `SecurityPolicy`, its HTTPS listener is restricted to hostname `gatus-probe.${SECRET_DOMAIN}` and the `observability` namespace, and the only new `HTTPRoute` is `gatus-probe` with a GET-only exact `/health` match. Cloudflare DNS must point the hostname to the existing tunnel, and Cloudflared must have an explicit probe ingress rule before its wildcard rule, using origin server name `gatus-probe.${SECRET_DOMAIN}`, HTTP/2 to the origin, and service `https://envoy-external-probe.network.svc.cluster.local:443`. After explicit rollout approval, establish behavior through Cloudflare Tunnel with `curl -fsS https://gatus-probe.${SECRET_DOMAIN}/health`; the request must reach Gatus's existing low-information `/health` endpoint, followed by Gatus success metrics/alerts and one pre-existing external application request that still reaches CrowdSec. The endpoint itself is the durable verification affordance: it provides a low-information, repeatable check across public DNS, Cloudflare Tunnel, TLS, Envoy, routing, and Gatus.

### Task 1: Remove the invalid source-IP egress design

**Files:**
- Modify: `talos/talconfig.yaml`
- Modify: `kubernetes/apps/kube-system/cilium/app/helmrelease.yaml`
- Modify: `kubernetes/apps/observability/gatus/app/helmrelease.yaml`
- Modify: `kubernetes/apps/observability/gatus/app/kustomization.yaml`
- Delete: `kubernetes/apps/observability/gatus/app/egress-gateway.yaml`
- Modify: `kubernetes/apps/security/crowdsec/bouncers/envoy/helmrelease.yaml`
- Delete: `docs/superpowers/specs/2026-07-18-gatus-stable-egress-design.md`
- Delete: `docs/superpowers/plans/2026-07-18-gatus-stable-egress.md`

- [ ] **Step 1: Remove only the obsolete Cilium egress label and policy reference.**

  Delete this block from the Gatus HelmRelease, leaving its existing resource and probe configuration unchanged:

  ```yaml
      podLabels:
        egress-gateway.cilium.io/gatus: "true"
  ```

  Remove `- egress-gateway.yaml` from `gatus/app/kustomization.yaml`, then delete `gatus/app/egress-gateway.yaml`.

- [ ] **Step 2: Restore the original network identity configuration.**

  Remove the `192.168.0.9/24` additional node address from `talos/talconfig.yaml`; remove the `egressGateway.enabled: true` value from the Cilium HelmRelease; and remove `ENVOY_BOUNCER_EXEMPTIPS: 192.168.0.9` from the CrowdSec Envoy bouncer HelmRelease. Do not alter CrowdSec trusted-proxy settings or its Gateway-level `SecurityPolicy`.

- [ ] **Step 3: Remove superseded planning artifacts.**

  Delete the July 18 stable-egress design and plan listed above. Keep `docs/superpowers/specs/2026-07-20-gatus-dedicated-probe-design.md` as the authoritative design record.

- [ ] **Step 4: Check the removal diff.**

  Run: `git diff --check`

  Expected: no whitespace errors; the Cilium egress policy, Gatus label, Talos secondary address, and bouncer exemption are absent.

### Task 2: Allocate and declare the dedicated Gateway address

**Files:**
- Modify: `kubernetes/components/common/cluster-settings.yaml:7-24`

- [ ] **Step 1: Confirm the prerequisite reservation.**

  Confirm that `192.168.0.9` is excluded from DHCP or reserved for Cilium LoadBalancer IPAM before any Flux reconciliation. This is an external network prerequisite; do not run Talos, Flux, or Cilium commands.

- [ ] **Step 2: Add the cluster substitution.**

  Insert this adjacent to the other Envoy addresses:

  ```yaml
    ENVOY_GATUS_PROBE_IP: 192.168.0.9
  ```

- [ ] **Step 3: Render the common component.**

  Run: `mise exec -- kustomize build kubernetes/components/common`

  Expected: rendered `cluster-settings` contains `ENVOY_GATUS_PROBE_IP: 192.168.0.9`.

### Task 3: Add the isolated external probe Gateway

**Files:**
- Modify: `kubernetes/apps/network/envoy-gateway/app/envoy.yaml:46-84`

- [ ] **Step 1: Add the Gateway after `envoy-external`.**

  Add a new `Gateway` named `envoy-external-probe` with a single HTTPS listener on port `443`, hostname `gatus-probe.${SECRET_DOMAIN}`, and the existing wildcard certificate secret `${SECRET_DOMAIN/./-}-production-tls`. Restrict `allowedRoutes.namespaces` to a `Selector` matching `kubernetes.io/metadata.name: observability`. Its annotations and infrastructure annotations must use the dedicated hostname and address:

  ```yaml
  metadata:
    name: envoy-external-probe
    annotations:
      external-dns.alpha.kubernetes.io/target: &probeHostname gatus-probe.${SECRET_DOMAIN}
  spec:
    gatewayClassName: envoy
    infrastructure:
      annotations:
        external-dns.alpha.kubernetes.io/hostname: *probeHostname
        lbipam.cilium.io/ips: ${ENVOY_GATUS_PROBE_IP}
    listeners:
      - name: https
        hostname: gatus-probe.${SECRET_DOMAIN}
        protocol: HTTPS
        port: 443
        allowedRoutes:
          namespaces:
            from: Selector
            selector:
              matchLabels:
                kubernetes.io/metadata.name: observability
        tls:
          certificateRefs:
            - kind: Secret
              name: ${SECRET_DOMAIN/./-}-production-tls
  ```

  Do not add this Gateway to the existing `crowdsec` `SecurityPolicy.targetRefs`, and do not create a second `SecurityPolicy`.

- [ ] **Step 2: Render the Envoy Gateway Kustomization.**

  Run: `mise exec -- kustomize build kubernetes/apps/network/envoy-gateway/app`

  Expected: `Gateway/envoy-external-probe` has only the HTTPS listener, the wildcard certificate reference, and `lbipam.cilium.io/ips: 192.168.0.9`; `SecurityPolicy/crowdsec` still targets only `Gateway/envoy-external`.

### Task 4: Route the isolated hostname to Gatus health

**Files:**
- Create: `kubernetes/apps/observability/gatus/app/gatus-probe-httproute.yaml`
- Modify: `kubernetes/apps/observability/gatus/app/kustomization.yaml:5-11`

- [ ] **Step 1: Create the exact-path HTTPRoute.**

  Create this route in the existing `observability` Flux target namespace. It targets Gatus's existing Service in the same namespace, so no `ReferenceGrant` is needed:

  ```yaml
  ---
  # yaml-language-server: $schema=https://k8s-schemas.home-operations.com/gateway.networking.k8s.io/httproute_v1.json
  apiVersion: gateway.networking.k8s.io/v1
  kind: HTTPRoute
  metadata:
    name: gatus-probe
  spec:
    hostnames:
      - gatus-probe.${SECRET_DOMAIN}
    parentRefs:
      - name: envoy-external-probe
        namespace: network
        sectionName: https
    rules:
      - matches:
          - path:
              type: Exact
              value: /health
            method: GET
        backendRefs:
          - name: gatus
            port: 80
  ```

- [ ] **Step 2: Include the route in the Gatus Kustomization.**

  Add `- gatus-probe-httproute.yaml` under `resources:` in `gatus/app/kustomization.yaml`, after `helmrelease.yaml`. Keep the existing secret, OCI repository, Prometheus rule, dashboard, and ConfigMap generator entries.

- [ ] **Step 3: Render the Gatus Kustomization.**

  Run: `mise exec -- kustomize build kubernetes/apps/observability/gatus/app`

  Expected: exactly one new `HTTPRoute` has hostname `gatus-probe.${SECRET_DOMAIN}`, parent `network/envoy-external-probe`, GET-only exact `/health` match, and backend `Service/gatus:80`.

### Task 5: Probe the isolated endpoint and stop external route discovery

**Files:**
- Modify: `kubernetes/apps/observability/gatus/app/helmrelease.yaml:52-64`
- Modify: `kubernetes/apps/observability/gatus/app/resources/config.yaml:25-58`

- [ ] **Step 1: Stop discovering the CrowdSec-protected external Gateway.**

  Remove only `- envoy-external` from `sidecar.gatewayNames`. Keep `envoy-internal` and `envoy-internal-tls`, preserving the existing internal route discovery behavior.

- [ ] **Step 2: Add the static public endpoint.**

  Insert this endpoint before the existing `Cloudflare` connectivity endpoint:

  ```yaml
  endpoints:
    - name: Gatus external probe
      group: external
      url: https://gatus-probe.${SECRET_DOMAIN}/health
      interval: 1m
      client:
        dns-resolver: tcp://1.1.1.1:53
      conditions:
        - "[STATUS] == 200"
      alerts:
        - type: discord
          enabled: true
          send-on-resolved: true
  ```

  Retain the existing ICMP connectivity and internal MCP endpoints unchanged.

- [ ] **Step 3: Render and inspect the Gatus configuration.**

  Run: `mise exec -- kustomize build kubernetes/apps/observability/gatus/app > /tmp/gatus-rendered.yaml && rg -n 'envoy-external|gatus-probe|egress-gateway.cilium.io' /tmp/gatus-rendered.yaml`

  Expected: `gatus-probe.${SECRET_DOMAIN}/health` is present; the obsolete egress label is absent; and `envoy-external` is absent from the sidecar `gatewayNames` output.

### Task 6: Validate the GitOps change and prepare rollout evidence

**Files:**
- Verify: all files changed above

- [ ] **Step 1: Run the repository Kubernetes validation.**

  Run: `bash .agents/skills/pr-review/scripts/validate-pr.sh`

  Expected: the validator completes successfully. If a pinned executable is missing, report the exact missing tool and run every available narrower render command from Tasks 2–5; do not claim full validation passed.

- [ ] **Step 2: Review the safety invariants.**

  Run: `git diff --check && git diff -- kubernetes/apps/network/envoy-gateway/app/envoy.yaml kubernetes/apps/security/crowdsec kubernetes/apps/observability/gatus talos/talconfig.yaml kubernetes/apps/kube-system/cilium/app/helmrelease.yaml`

  Expected: no CrowdSec policy targets `envoy-external-probe`; the existing policy still targets `envoy-external`; and no Talos secondary address, Cilium egress gateway enablement, egress policy, or bouncer exempt IP remains.

- [ ] **Step 3: Record the approved live verification sequence without executing it.**

  After separate user approval for live changes, use Flux to reconcile the Envoy Gateway owner before Gatus, then verify:

  ```bash
  curl -fsS https://gatus-probe.${SECRET_DOMAIN}/health
  mise exec -- kubectl -n observability get httproute gatus-probe
  mise exec -- kubectl -n network get gateway envoy-external-probe
  ```

  Confirm Gatus exposes a successful endpoint metric for `Gatus external probe`, `GatusEndpointDown` no longer fires for the removed application routes, and an ordinary request to an existing `envoy-external` application route still receives CrowdSec evaluation. Do not run these commands, Flux reconciliation, or any Talos operation without explicit approval.
