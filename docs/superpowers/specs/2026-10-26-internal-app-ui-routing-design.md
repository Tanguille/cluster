# Internal app UI routing

Approved in conversation: expose Unpackerr and Agentgateway on the trusted LAN through `envoy-internal`, with hostnames `unpackerr.${SECRET_DOMAIN}` and `agentgateway.${SECRET_DOMAIN}`. Keep Unpackerr's existing password; Agentgateway's Kubernetes UI is read-only and will have no additional authentication.

## Approach

Use HTTPRoutes and existing k8s-gateway internal DNS discovery, not public DNS or an external Gateway. Preserve Agentgateway's existing `llm.${SECRET_DOMAIN}` API route. Port-forwarding remains a private alternative but does not meet browser-hostname access requirements.

Agentgateway requires an admin listener bind change plus a separate selector Service on port 15000. Verify UI asset and API paths before restricting the HTTPRoute: a route serving only the HTML must not leave the UI broken. Limit exposed paths to those needed where practical. Widening the bind makes the admin listener reachable to pods already permitted by existing network policies; it is not an authentication boundary.

No secret edits, authentication redesign, public exposure, homepage redesign, push, or live reconciliation. Local rendering establishes manifest validity; live DNS/browser verification requires subsequent authorized deployment.

## Implementation finding

The deployed v1.6.0 runtime reports xDS gateway mode but file config-store mode. Do not rely on the upstream description of a read-only Kubernetes UI as an authorization boundary: the new browser route explicitly excludes configuration writes and other administration operations by path and HTTP method. Direct access to the widened admin listener is still governed by existing namespace network policies. Trusted LAN users can read configuration and available log information.
