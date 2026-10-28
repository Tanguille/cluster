# Renovate security hardening decisions

Branch `harden/kguardian-renovate-security`; worktree
`.claude/worktrees/kguardian-renovate-security`, based on main `5f33d7baf`.
Network changes were split from PR #6265 head `2535e0525`.
**Draft only: these CNPs enforce if merged. No live changes were made.**

## Workload decisions

All observed containers already have non-root execution, no privilege escalation,
drop ALL, read-only root and RuntimeDefault from their existing chart/operator
settings. kguardian returned no recommended securityContext patch. Preserve these
defaults rather than duplicate them. The only syscall finding is absence of a custom
SeccompProfile CR; it does not mean RuntimeDefault is absent.

PSS `restricted` below is only an upper bound: kguardian evaluates 9 of 18 checks.
Volume types/hostPath, host ports/probes, AppArmor, SELinux, procMount, sysctls
and hostProcess remain unobserved. No restricted-compliance claim is made.

| Workload | Posture; coverage; unknownDimensions | PSS upper bound | Recommendation decision |
| --- | --- | --- | --- |
| Deployment / renovate-operator | warn; 0.25; `[network, podSecurity, images]` | restricted | Already hardened; API token required for controller operations. |

Retained Jobs and scheduled execution still require their own startup and runtime
checks; a controller profile does not certify its generated Jobs.

## Other kguardian features (2026-10-28)

These are sanitized read-only results, not live hardening or an audit pass.
The profile table above quotes posture coverage and unknown dimensions; unknown
never means safe. Raw pod addresses, credentials and captures are not published.

| Feature used | Evidence and decision |
| --- | --- |
| Workload inventory, profiles and revision diffs | 1 controller profiles and diffs reviewed; revision details below. Observed churn is not necessarily configuration drift. |
| Namespace traffic | 0 records, 0 pods, 0 drops at this snapshot. Coverage is incomplete; a rolling count is not a before/after enforcement delta. |
| Image inventory | 1 running digests in the returned inventory; inventory truncated: false. Inventory is not a vulnerability verdict. |
| Vulnerabilities and exposure | kguardian's HIGH/CRITICAL query returned no findings because its image reports are absent. Separate Trivy findings appear below; kguardian exposure evaluation still needs ingested reports. This is not zero vulnerabilities. |
| SBOM and signer queries | Queried all 1 returned running digests: no SBOM reports, and signers unknown/not_checked. This is neither unsigned nor verified/trusted. No trusted issuer/subject is inferred. |
| Image-trust explanation | Evaluation unavailable: signature discovery is off. No ImageTrustPolicy is added without verified signer evidence and owner-selected trust roots. |
| Syscalls and profile generation | Representative `renovate-operator` pod returned 33 observed x86_64 syscalls. Generation failed with the architecture error below; no custom profile is bound. |
| Seccomp denials | Profile counters have zero observed denials. No SeccompProfile CR exists; this is not proof of complete enforcement coverage or safe custom allowlists. |
| Compute findings | 1 victims evaluated; 0 findings; truncated=false, history_disabled=false. This is a bounded observation window. |
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
| Deployment / renovate-operator | 28 → 29 | network | 1 added: egress TCP/6443 |

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

[Policy](../../kubernetes/apps/renovate/networkpolicy.yaml) gives the operator internal
UI 8081, public webhook 8082, vmagent metrics 8080, API, Dragonfly 6379 and HTTPS.
Stable discovery/executor Job labels allow cache and public HTTPS, without API
access. Only executors retain observed TCP/80; verify those destinations. The
chart's admission policy is unrelated and remains enabled.

No Kubernetes CronJob exists: the RenovateJob runs every 30 minutes and the
snapshot contained four Jobs. Audit every repository/datasource, discovery,
tool downloads, token refresh, cache and webhook synchronization. Capture must
include the namespace; the unmerged write-rate branch proposed excluding it.

## Local validation

`mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh` passed:
full Flux/Helm rendering, shellcheck, repository secret guardrails and all 25
regression tests. No live deployment or seven-day audit was performed.

## Trivy follow-up, 2026-10-28

The [image ledger](trivy-renovate-images.csv) records the operator and generated
Job images separately. MCP confirms the operator's running digest matches its
2026-10-26 report. The [finding ledger](trivy-renovate-findings.csv) preserves its
one High finding: CVE-2026-78669 in `golang.org/x/net` v0.59.0, fixed in v0.60.0.
The newest stable publisher release 6.7.0 was checked, but its
[tagged source](https://github.com/mogenius/renovate-operator/blob/01b4299e9bd2af6f8a6dfcbed8cff65610cc64bf/src/go.mod)
still selects v0.59.0. Its newer Go compiler does not update that separate module.
No ineffective security bump is included; a publisher dependency fix and a
matching fresh scan remain required.

The generated Renovate Job image has no matching report in the captured inventory
and remains **unknown**, not clean. Current namespace config/RBAC reports contain
no failed Critical/High checks, and MCP binding inspection found no additional
cluster-role grant to a Renovate service account. Keep `ownNamespaceOnly: true`
and the operator's Job/API permissions. No scanner exclusions, ignores, custom
images or live changes are introduced.

The snapshot above observed the Renovate job image 44.190.2. Main subsequently
advanced that job to 44.191.0 in #6300; that replacement has no verified report
in this collection. This docs-only follow-up does not revert the image update.
