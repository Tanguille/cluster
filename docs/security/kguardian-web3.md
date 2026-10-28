# Web3 security draft

Local proposal on `harden/kguardian-web3-security`, worktree
`.claude/worktrees/kguardian-web3-security`, based on `5f33d7baf`.
The namespace CNP and Helm values are enforcement candidates. Neither local
validation nor a draft PR authorizes deployment. No workload was changed,
restarted or scaled for this review.

## Evidence and its limits

The 2026-10-28 review used namespace traffic, all seven workload security profiles
and revision diffs, audit verdicts, compute findings, image inventory,
vulnerability listings and image-trust explanations. Recommendations were checked
against Helm/ResourceSet templates and digest-pinned monerod image metadata.
Raw observations stay outside this public repository.

PSS levels below are **upper bounds only**, never a restricted-compliance claim:
kguardian evaluates only nine of eighteen checks. No custom SeccompProfile
currently enforces the observed syscall set. RuntimeDefault is a different
runtime profile and does not resolve that missing-CR finding.

No dangerous drift item was reported, but `unshippedExecutable` is not evaluated
without executable inventory and `securityContextRegression` lacks a baseline.
Both remain **unknown**. The miner revision changes from null to known container
context reflect observation/scale lifecycle; they are not proof of a privilege
regression or complete coverage. Keep the crypto-miner guard history in scope.

Audit verdicts were empty without a completed audit window. Image trust is
unavailable, and zero reported HIGH/CRITICAL vulnerabilities excludes images
without reports. Both are unknown, not an all-clear. Compute findings returned
no findings across seven evaluated victims; that bounded result does not justify
changing mining quotas, thermal thresholds or scale decisions.

## Recommendation decisions

“Applied” means added to this local draft; “already” means present in current
configuration. Every posture includes its reported coverage and unknown
dimensions. Deferred settings need a separate compatibility trial.

| Workload | Posture; coverage; unknown dimensions | PSS upper bound | Decision |
| --- | --- | --- | --- |
| Deployment/monerod | warn; 50%; network, images | baseline | Applied: RuntimeDefault and escalation disabled. Exempt: root startup and ownership/UID/GID capabilities. Deferred: non-root enforcement, drop ALL and read-only root. |
| Deployment/p2pool | warn; 50%; network, images | baseline | Applied: RuntimeDefault. Already: non-root, escalation disabled and drop ALL. Deferred: changing its explicitly writable root until all runtime data paths are exercised. |
| Deployment/dashboard | warn; 50%; network, images | baseline | Applied: pod RuntimeDefault for both Python and nginx containers. Already: non-root, escalation disabled, drop ALL and read-only root; shared PVC group stays unchanged. |
| Deployment/guard | warn; 25%; network, podSecurity, images | restricted | Already: non-root, RuntimeDefault, escalation disabled, drop ALL and read-only root. No additional patch. |
| Deployment/xmrig-control-1 | warn; 50%; network, images | baseline | Applied: RuntimeDefault through the shared ResourceSet Helm values. Already: non-root, escalation disabled, drop ALL and read-only root. Preserve no-huge-pages and thermal/solar scaling. |
| Deployment/xmrig-control-2 | warn; 50%; network, images | baseline | Applied: same ResourceSet RuntimeDefault setting. Already: hardened UID/container context. Preserve all mining safeguards. |
| Deployment/xmrig-control-3 | warn; 50%; network, images | baseline | Applied: same ResourceSet RuntimeDefault setting. Already: hardened UID/container context. Preserve all mining safeguards. |

RuntimeDefault is already the repository's declared Talos kubelet default in
`talos/cluster.yaml.j2`; explicit workload values make that intent reviewable.
This does not verify the active kubelet configuration or replace runtime testing.
No privileged access, hugepage permission, resource limit, replica count, KEDA
fallback, guard threshold, pod placement or GPU setting is changed.

The exact monerod image has no non-root USER and records revision
`7f42fce576baac3d59b5e2a594583758ec449e11`.
Its [entrypoint](https://github.com/sethforprivacy/simple-monerod-docker/blob/7f42fce576baac3d59b5e2a594583758ec449e11/entrypoint.sh)
normalizes data ownership as root, then uses `su-exec` to run as the configured
UID/GID. Escalation-disabled execution preserves those already-held capabilities
and the privilege drop. Generic drop-ALL or non-root startup would remove that
ownership repair path. Retain it until a separate PVC/startup test supports a
replacement. The data and log mounts remain writable.

## Validation and approval gates

Local validation passed: `mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh`
completed the full Flux/Helm render, shellcheck, secret guardrails and 25 regression
tests. Pinned app-template 5.3.0 renders verified all seven Deployments, expanding
the three ResourceSet inputs. Assertions checked both dashboard containers,
unchanged guard/KEDA state, miner no-huge-pages arguments and monerod's preserved
startup permissions. Static success is not runtime validation.

Before enforcement, require the owner's approval and at least seven days of
audit evidence including miner scale-up/down, guard failures, reconnects,
P2P churn and dashboard fallbacks. No scale-up was induced for data collection.
Resolve miner donation destinations/ports: the command does not explicitly
disable donation and current evidence does not justify a guessed Internet grant.
Verify LAN LoadBalancer source ranges and observed source identity. Preserve the
deny-apiserver component. Collect executable inventory and security-context
baselines before treating drift as clear. Custom seccomp and image-trust
artifacts require their own review and approval before deployment.

## Other kguardian features (2026-10-28)

These are sanitized read-only results, not live hardening or an audit pass.
The profile table above quotes posture coverage and unknown dimensions; unknown
never means safe. Raw pod addresses, credentials and captures are not published.

| Feature used | Evidence and decision |
| --- | --- |
| Workload inventory, profiles and revision diffs | 7 controller profiles and diffs reviewed; revision details below. Observed churn is not necessarily configuration drift. |
| Namespace traffic | 3 records, 1 pods, 0 drops at this snapshot. Coverage is incomplete; a rolling count is not a before/after enforcement delta. |
| Image inventory | 6 running digests in the returned inventory; inventory truncated: false. Inventory is not a vulnerability verdict. |
| Vulnerabilities and exposure | Namespace HIGH/CRITICAL query returned no findings; running-image reports are absent. No evidence-backed CVE id exists for `explain_cve_exposure`; do not invent one or claim zero vulnerabilities. |
| SBOM and signer queries | Queried all 6 returned running digests: no SBOM reports, and signers unknown/not_checked. This is neither unsigned nor verified/trusted. No trusted issuer/subject is inferred. |
| Image-trust explanation | Evaluation unavailable: signature discovery is off. No ImageTrustPolicy is added without verified signer evidence and owner-selected trust roots. |
| Syscalls and profile generation | Representative `monerod` pod returned 108 observed x86_64 syscalls. Generation failed with the architecture error below; no custom profile is bound. |
| Seccomp denials | Profile counters have zero observed denials. No SeccompProfile CR exists; this is not proof of complete enforcement coverage or safe custom allowlists. |
| Compute findings | 7 victims evaluated; 0 findings; truncated=false, history_disabled=false. This is a bounded observation window. |
| Audit verdicts | WouldDeny query empty; live inventory contains no AuditNetworkPolicy/AuditClusterNetworkPolicy CRs. No audit has started or passed. |

The MCP `generate_seccomp_profile` call returned `unrecognized architecture —
no seccomp arch mapping (supported: x86_64, aarch64)`, even though
`get_pod_syscalls` reports x86_64 for the same pod. Treat this as an export blocker.
Keep RuntimeDefault; do not replace it with an unverified allowlist or LOG profile.
A workload aggregate can include older pods and differs from this single-pod set.
Resolve the exporter and capture startup, Jobs and rare paths before proposing
a custom profile; no generated syscall set is claimed complete over seven days.

No `unshippedExecutable` or `securityContextRegression` drift item was returned.
Executable inventory and a context/export baseline are missing for these checks;
that is unknown, not a clean drift result. Investigate any future positive finding
before advancing enforcement.

### Revision evidence

Latest retained revision pairs were compared, not a seven-day continuous history.
Peer addresses are deliberately omitted. New ports require attribution; an added
HTTPS peer alone does not identify its hostname or justify a broader grant.

| Workload | Revisions | Changed dimensions | Network observations |
| --- | --- | --- | --- |
| Deployment / dashboard | 69 → 70 | images | No peer changes |
| Deployment / guard | 45 → 46 | images | No peer changes |
| Deployment / monerod | 2058 → 2059 | network | 1 added: egress TCP/18080; 1 removed: egress TCP/18080 |
| Deployment / p2pool | 567 → 568 | network | 1 added: egress TCP/37890 |
| Deployment / xmrig-control-1 | 170 → 171 | images, podSecurity | No peer changes |
| Deployment / xmrig-control-2 | 122 → 123 | network | 1 added: egress TCP/443 |
| Deployment / xmrig-control-3 | 97 → 98 | images, podSecurity | No peer changes |

### Feature prerequisites and audit gate

Pinned kguardian chart 1.29.2 defaults and live settings were inspected.
PR #6264 (`fix/kguardian-write-rate`) remains open and unmerged at this check;
evaluator and compute are enabled, syscall capture is full, and `llmBridge` remains
enabled. Recheck before starting an audit: that proposal disables the evaluator
and excludes additional namespaces. Do not enable expensive capture implicitly.

The installed AuditNetworkPolicy/AuditClusterNetworkPolicy schemas use native
Kubernetes NetworkPolicy peers/selectors/ports. They cannot faithfully express
Cilium entities, service-account identities, selectorless Services, ICMP or DNS/L7
restrictions and Cilium deny precedence. A lossy conversion would produce false
confidence. No purported equivalent audit CR is included. Establish equivalent
evaluation or independently test unsupported dimensions before approval.

Supply-chain ingestion is off. In this chart it requires scoped broker auth with
a supplychain token and an actual report source (for example Trivy Operator; a
Trivy server alone does not supply operator reports). Signature discovery and
image-trust evaluation are separate opt-ins. Runtime executable inventory is
`controller.runtimeInventory.mode: off`, with capability tracing also off; enabling
them needs an overhead/write-rate check and owner approval. No secrets or global
kguardian settings are changed by this namespace PR.

Before merging the enforcing CNPs: refresh live settings and workloads; obtain
approval for the audit deployment; collect at least seven complete days including
the rare paths below; fix every legitimate WouldDeny with evidence; validate the
unsupported Cilium dimensions; then obtain explicit enforcement approval. Watch
drop deltas and app health after an approved rollout. Static rendering and an
empty query are not runtime validation.

## Network policy and rare-path review

[Policy](../../kubernetes/apps/web3/networkpolicy.yaml) separates monerod, p2pool,
miners, dashboard and guard. Monerod/p2pool retain variable-port world P2P egress;
miners get stratum 3333 and dashboard API ingress 42000. Guard reaches vmauth 8427
and exposes metrics only. LoadBalancer listener grants still depend on existing
`${LAN_CIDR}` source ranges and preserved client identity; `world` is not LAN-only.

All three miners were scaled to zero. Audit scale-up/down, reconnects, peer churn,
dashboard fallbacks and monitoring failures. XMRig donation is not explicitly
disabled; its unobserved destinations need investigation before any internet grant
or owner-approved command change. No scaling or guard thresholds changed.
