# Accepted Debt

Deliberate stopgaps that work today and are documented where they live, but have no other
tracking home. Each one has a condition that makes it wrong; without a ledger, that condition
gets met and nobody notices. Entries leave here when the condition fires or the workaround dies.

| What | Where | Break condition |
| --- | --- | --- |
| memini is registered as an `MCPServerEntry` pointing at an in-cluster `.svc` address, which only works because ToolHive's SSRF blocklist hard-lists `.svc.cluster.local` and friends but not bare `.svc`. Not a supported registration path: `MCPServer` cannot adopt an existing Service, and `MCPRemoteProxy`/`MCPServerEntry` are remote-only by design. | `kubernetes/apps/ai/toolhive/config/memini.yaml` | A ToolHive release tightening the blocklist to `.svc`. Real fix is upstream: a way to adopt an existing in-cluster Service. |
| memini's `/v1/handshake` returns `admin=true, read_only=false` to any caller that can reach it, so a CiliumNetworkPolicy is providing network-level access control in place of the authn the app does not have. Reachability is the only thing gating access, and it does not identify the caller. | `kubernetes/apps/kube-system/network-policies/app/memini-ingress.yaml` | Belongs in memini itself. The CNP is the right compensating control here, but it is filling a hole, not closing one. |
| talhelper is replaced by a hand-rolled `just` + minijinja render pipeline, because talhelper's machinery pin panics against Talos 1.14 rc.1's `UnattendedInstallConfig`. Deliberate and documented (see `talos/README.md` "Why not talhelper"), modeled on onedr0p/home-ops. | `talos/mod.just`, `talos/*.yaml.j2` | talhelper shipping a 1.14-compatible release. Revisit then, so bespoke render infra does not become permanent by default. |
