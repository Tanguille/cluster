# Gatus dedicated probe endpoint design

**Status:** Approved design

## Decision summary

Provide Gatus with one dedicated public HTTPS endpoint,
`gatus-probe.${SECRET_DOMAIN}`, rather than changing the source identity of
Gatus traffic. The endpoint is served by a separate Envoy Gateway
`LoadBalancer` and routes to Gatus's existing low-information `/health`
endpoint. It has no CrowdSec `SecurityPolicy`.

Gatus will probe this endpoint over HTTPS and will stop generating external
application-route probes. Existing public application routes keep their
current Envoy Gateway and CrowdSec protection.

## Motivation

Gatus external probes currently pass through the public application routing
path. CrowdSec evaluates the client address from `X-Forwarded-For`; for the
observed request this was `185.77.13.253`. The bouncer therefore returned HTTP
`418` when that address had a CrowdSec decision, even though the request was a
cluster health check. The probe endpoint separates health checking from
application traffic instead of weakening source-address handling or exempting
a broad network range.

## Approved design

### Dedicated public endpoint

- Publish `gatus-probe.${SECRET_DOMAIN}` as a dedicated public HTTPS hostname.
- Allocate a separate Envoy Gateway `LoadBalancer` for this hostname and its
  listener/route.
- Route only `GET /health` to Gatus's existing low-information health endpoint.
- Do not create a separate `/healthz` backend. The existing endpoint only needs
  to indicate that Gatus is available and must not expose credentials, user
  data, application state, or diagnostics.
- Do not attach the CrowdSec `SecurityPolicy` to this dedicated route. This is
  a narrowly scoped probe surface, not an exemption from CrowdSec for existing
  application routes.

The Cloudflared configuration has an explicit `gatus-probe.${SECRET_DOMAIN}`
ingress rule before the wildcard rule. It uses origin server name
`gatus-probe.${SECRET_DOMAIN}`, enables HTTP/2 to the origin, and sends traffic
to `https://envoy-external-probe.network.svc.cluster.local:443`.

The dedicated HTTPS listener is restricted to hostname
`gatus-probe.${SECRET_DOMAIN}` and allows routes only from the `observability`
namespace via a namespace selector. The HTTPRoute is GET-only and matches the
exact `/health` path.

### Gatus configuration

Configure Gatus to probe the HTTPS `gatus-probe.${SECRET_DOMAIN}/health`
endpoint. Remove or disable the generated external probes for public
application routes; Gatus must not continue probing those routes as a second
path. Preserve internal and service probes unless they are explicitly part of
the generated external route set being removed.

### Existing public traffic

All existing public application hostnames continue using their current Envoy
Gateway listeners, routes, and CrowdSec policy attachments. No public
application route receives the dedicated endpoint's CrowdSec omission.

## Boundaries and non-goals

This design:

- isolates only the Gatus health check from application-route bouncer
  evaluation;
- keeps the existing Gatus `/health` response low-information;
- uses GitOps-owned Kubernetes resources and Flux reconciliation; and
- leaves source IP forwarding and CrowdSec trust configuration unchanged.

This design does not:

- exempt the Pod CIDR, a node subnet, the LAN, or all traffic from Gatus;
- add a Talos secondary address such as `192.168.0.9`;
- enable or configure Cilium Egress Gateway for this purpose;
- add a CrowdSec bouncer exemption for a node, pod, or dynamic egress IP;
- remove CrowdSec protection from existing public application routes;
- make the health endpoint a general-purpose proxy or application API; or
- perform a live cluster rollout as part of this change.

## Manifest ownership

The implementation should remain split by existing repository ownership:

- `kubernetes/apps/network/envoy-gateway/app/` owns the dedicated Envoy
  Gateway `LoadBalancer`, listener, and HTTP routing resources.
- `kubernetes/apps/observability/gatus/app/` owns the Gatus configuration and
  HTTPRoute targeting Gatus's existing `/health` endpoint.
- The existing CrowdSec resources under
  `kubernetes/apps/security/crowdsec/` remain unchanged for this design.
- `talos/talconfig.yaml` and the Cilium HelmRelease remain unchanged; no
  network identity or egress policy is required.

The dedicated route must be visibly separate from existing application routes
so that its lack of a CrowdSec `SecurityPolicy` cannot accidentally broaden
to other listeners or parent resources.

## Rollout order

This document specifies a GitOps change only. Do not run Talos apply, Flux
reconciliation, or any other live rollout while implementing or reviewing the
design.

When a separate live-rollout approval is granted, reconcile in this order:

1. Apply the Cloudflare Tunnel DNS, Cloudflared ingress, Envoy Gateway, and
   HTTPRoute resources through Flux.
2. Confirm the HTTPS hostname resolves through Cloudflare Tunnel and that the
   tunnel reaches `envoy-external-probe`, where `GET /health` reaches Gatus's
   existing low-information health endpoint.
3. Reconcile the Gatus configuration and confirm Gatus is probing only the
   dedicated endpoint for the external check.
4. Confirm existing public routes still have their CrowdSec policy and that
   the dedicated route has none.

## Validation criteria

Before any live rollout, validate manifests and rendered resources locally:

- the dedicated Gateway is a separate `LoadBalancer` behind Cloudflare Tunnel
  and has the expected HTTPS hostname;
- the HTTP route is GET-only, matches only the intended `/health` path, and
  targets Gatus's existing health endpoint;
- Cloudflared has the explicit probe ingress rule before the wildcard rule;
- the probe HTTPS listener has the expected hostname and observability-only
  namespace restriction;
- no CrowdSec `SecurityPolicy` is attached to the dedicated route;
- existing public routes and their CrowdSec policy references are unchanged;
- Gatus's existing `/health` endpoint remains low-information and has no new
  sensitive configuration or persistent state; and
- Gatus no longer contains generated external application-route probes and
  contains the dedicated HTTPS health probe.

After an approved rollout, verify the endpoint from outside the cluster,
check Gatus success metrics and alerts, and make one ordinary request to an
existing public application route. The latter must remain subject to CrowdSec
evaluation; only the dedicated Gatus probe endpoint is intentionally outside
that policy.

## Security considerations

The endpoint is reached through Cloudflare Tunnel, so Gatus's `/health`
response must disclose only low-information availability and must not echo
request data, expose diagnostics, or provide access to internal services. The separate `LoadBalancer` and route
limit the policy exception to one hostname and one path. Existing application
routes retain CrowdSec enforcement, and no broad CIDR or unstable workload
identity is trusted.
