# Web3 network policy draft

Local Phase 2 draft on `harden/kguardian-media-web3`, worktree
`.claude/worktrees/kguardian-media-web3`, based on `fdd3cd954`. No push, PR, cluster
apply or reconciliation was performed. The CNP file is an enforcement candidate;
**do not merge it before the separate audit and owner approval**.

## Evidence and scope

Read-only inventory on 2026-10-28 showed four active pods: dashboard, guard,
monerod and p2pool. Their kguardian-generated Cilium policies were cross-checked
against Services, route configuration, dashboard Python clients and xmrig
ResourceSet pod templates. All three thermal-guarded miners were scaled to zero,
so they have no current pod for policy generation. Historical dashboard/p2pool
flows identify miners, but do not establish current coverage for scale-up.

The traffic summary returned only one pod, five records and one pre-existing
drop. The generated histories are wider but truncated for P2P. Neither source
provides seven days of complete coverage. Missing executable/security-context
baselines leave drift unknown; this draft does not clear the mining guard's
security concerns.

The namespace policy replaces unrestricted same-namespace and broad observer
access with these app-specific paths, plus host probes, DNS TCP/UDP 53 and
unported kube-dns reply ingress:

| Workload | Allowed paths |
| --- | --- |
| monerod | P2Pool RPC/ZMQ 18089/18083; dashboard restricted RPC 18089; existing LoadBalancer listener ports; world egress for peers that advertise arbitrary ports. |
| p2pool | Miners stratum 3333; existing LoadBalancer stratum/P2P ports; monerod RPC/ZMQ; world egress for variable P2P ports. |
| xmrig miners | P2Pool 3333 and DNS egress; dashboard API ingress 42000. The stable `thermal-guarded` label covers all three dynamic Deployments. |
| dashboard | Internal Envoy ingress 80; monerod 18089, miner APIs 42000 and documented observer/exchange/FX APIs over HTTPS. |
| guard | vmagent metrics ingress 8080; vmauth egress 8427. No Internet or Kubernetes API grant. |

LoadBalancer Services keep their existing `${LAN_CIDR}` source ranges and
`externalTrafficPolicy: Local`. The app-scoped `world` ingress rules preserve
their external client identity; they are bounded to those listener ports but
depend on the Service source-range gate. No P2P peer IP is committed. There are
no web3 CronJobs or Jobs in the inventory and no current Kopiur backup binding.
The API-deny component and its existing Kopiur exception remain unchanged.

Live inventory showed only the namespace allowlist and API-deny CNP in web3,
with no native NetworkPolicies. The broad namespace union was removed. No
thermal guard thresholds, autoscaling, mining command, or securityContext changed.

## Audit readiness blockers

- Observe at least seven days including miner scale-up/down, stratum reconnect,
  P2P churn, dashboard fallbacks and monitoring failures. Current scale-zero
  miners and truncated P2P histories cannot justify enforcement.
- Resolve the miners' XMRig donation behavior before enforcement. Their checked-in
  command does not explicitly disable donation; no observed current miner
  egress establishes its destinations or ports. The draft deliberately has no
  guessed Internet exception. Audit the configured image and a scale-up before
  choosing an owner-approved grant or command change.
- Confirm LAN LoadBalancer connectivity and source identity at audit time;
  check the source ranges remain effective. World ingress on listener ports is
  not itself a LAN CIDR restriction.
- Verify the installed evaluator and the write-rate branch state before creating
  audit resources. Preserve `llmBridge`; evaluator changes and audit deployment
  require owner approval. No audit CR has been deployed by this work.
- Correlate every legitimate `WouldDeny` with workload/configuration evidence,
  and investigate new executable or security-context drift first. Retain the
  baseline drops for comparison; a nonzero counter predates this draft.

## Validation

`mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh` passed Flux/Helm
rendering, shellcheck, secret guardrails and 24 regression tests. The deployed
Cilium CRD schema accepts the policy. Selector checks cover all seven web3
Deployment templates, including scale-zero miners. Across media and web3, 62
generated current internal egress edges, 54 current ingress edges and 24
positive/negative cases pass the static policy-union check. `git diff --check`
passes. Static checks do not prove audit readiness or runtime safety.

Next phase requires owner confirmation. Suggested continuation: “Prepare web3
AuditNetworkPolicy resources and the miner donation investigation for review;
do not deploy or scale miners.”
