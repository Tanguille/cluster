# Renovate namespace network policy draft

Phase 2, 2026-10-28. Branch `harden/kguardian-controllers`, worktree
`.claude/worktrees/kguardian-controllers`, base `fdd3cd954`.
Local proposal only: the manifests are real CNPs and would enforce if deployed.
No push, PR, apply, reconciliation or audit deployment has occurred.
Verify branch/worktree before continuing.

## Evidence and changes

Five current pods produced generated policies: the operator, one discovery Job
and three dependency-update Jobs. The namespace traffic query contained only
24 records for one pod, with zero reported drops. This is partial coverage,
not proof that the namespace has no drops. The earlier operator profile was
warn, coverage 25%, unknown network/podSecurity/images; its retained network
span began October 16 and does not cover all separate Jobs.

The namespace had no native or Cilium network policy. The new namespace CNP
defaults both directions to deny with kube-dns request/reply and host-probe
exceptions. Per-role paths are:

| Role | Ingress | Egress beyond DNS |
|---|---|---|
| Operator | Internal gateway UI 8081, external gateway webhook 8082, vmagent metrics 8080 | Kubernetes API 443/6443, Dragonfly 6379, public HTTPS 443 |
| Discovery Job | No application ingress | Dragonfly 6379, public HTTPS 443 |
| Executor Jobs | No application ingress | Dragonfly 6379, public HTTPS 443 and observed HTTP 80 |

The stable Job selectors use the RenovateJob name and discovery/executor type;
they do not pin job hashes, generation numbers or repository identities.
The operator's chart admission `policy.enabled` is unrelated and remains on.
No existing API-deny overlay was removed. Jobs receive no API allow grant.

There were no Kubernetes CronJobs, but four retained Jobs are controlled by the
Renovate operator. The RenovateJob schedule runs every 30 minutes. Its discovery,
repository update and webhook synchronization paths must all be exercised.

## Validation and audit gate

The required `validate-pr.sh` passed flate, shellcheck, secret guardrails and
24 regression tests. The deployed Cilium CRD schema, all current subject/peer
selectors, baseline coverage and representative role-isolation checks passed.

An executor's generated policy included public TCP/80. The draft preserves it
only for executors; the destination's purpose is unverified and needs review
during audit. Registry/provider HTTPS needs can change with dependency files;
concrete observed addresses are not copied into the policy.

Collect at least seven days of AuditNetworkPolicy evidence before enforcement,
including every configured repository and datasource, discovery, cache access,
dependency-tool downloads, GitHub app token refresh and webhook sync. Confirm
successful runs as well as WouldDeny results; silence is insufficient coverage.
Evaluator/capture must still include this namespace: the unmerged kguardian
write-rate branch proposed excluding it.

Next phase requires owner confirmation. Suggested continuation: "Prepare the
Renovate audit proposal from this draft; keep deployment gated on my approval."
