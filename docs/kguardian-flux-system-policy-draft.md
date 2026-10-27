# Flux namespace network policy draft

Phase 2, 2026-10-28. Branch `harden/kguardian-controllers`, worktree
`.claude/worktrees/kguardian-controllers`, base `fdd3cd954`.
Local proposal only: the manifests are real CNPs and would enforce if deployed.
No push, PR, apply, reconciliation or audit deployment is authorized by this draft.
Verify branch/worktree before continuing.

## Evidence and changes

Generated policies were collected for all six current pods after querying
namespace traffic. That traffic query returned **zero records and zero pods**,
despite per-pod historical policies returning flows. This is missing coverage,
not a clean traffic baseline. Konflate's earlier profile was warn, coverage 25%,
unknown network/podSecurity/images; its retained network span began October 13.
A retained span alone does not establish seven days of complete capture.

The namespace had only Konflate's native NetworkPolicy; FluxInstance and
flux-operator chart policies were already disabled. The proposal replaces
Konflate's chart policy to avoid its broader additive ingress grant.

| Workload | Allowed application paths beyond DNS/probes |
|---|---|
| source-controller | Helm/Kustomize clients to artifact pod port 9090; metrics from vmagent 8080; API, notification events 9090 and external HTTPS out |
| helm-controller, kustomize-controller | Source artifacts 9090, API, notification events 9090 and external HTTPS out; vmagent metrics 8080 in |
| notification-controller | Flux controllers/operator events 9090; external gateway webhook 9292; metrics 8080; API/provider HTTPS out |
| flux-operator | Internal gateway UI 9080; metrics 8080; API, notification events 9090 and distribution/resource HTTPS out |
| Konflate | External gateway and AI unified MCP 8080; metrics 8081; public source/chart/image HTTPS out |

Live Service targets verified the 80/443-to-9090 artifact/event and
80/443-to-9292 webhook translations. Unified's pod name is not its app label:
the selector is `name=virtualmcpserver, instance=unified`. The namespace baseline
denies both directions except explicit grants, allows host probes, and permits
unported kube-dns reply ingress. Konflate gets no API grant. Existing API-deny
components elsewhere are untouched; this namespace did not have one.

No CronJobs or Jobs existed in the namespace snapshot, and these manifests do
not configure Kopiur movers. Image pulls originate from nodes, not app pods.

## Validation and audit gate

The required `validate-pr.sh` passed flate, shellcheck, secret guardrails and
24 regression tests. The deployed Cilium CRD schema, every current subject/peer
selector, default-deny coverage and representative allowed/denied paths passed
local checks. These do not prove live datapath behavior.

Before enforcement, express the proposal as AuditNetworkPolicy resources and
verify evaluator support, then observe at least seven days including source
refreshes, chart installation/upgrades, remote Kustomize bases, receiver hooks,
outbound provider notifications, Konflate image verification and a cold render.
Resolve every legitimate WouldDeny with evidence. HTTPS endpoints are grouped
as `world:443` for roles that fetch public content; individual remote hosts were
not authenticated by this flow capture. No FQDN allowlist is inferred.

Next phase requires owner confirmation. Suggested continuation: "Prepare the
Flux audit proposal from this draft; keep deployment gated on my approval."
