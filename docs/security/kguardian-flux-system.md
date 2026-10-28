# Flux system security hardening decisions

Branch `harden/kguardian-flux-system-security`; worktree
`.claude/worktrees/kguardian-flux-system-security`, based on main `5f33d7baf`.
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
| Deployment / source-controller | warn; 0.25; `[network, podSecurity, images]` | restricted | Already hardened; API token required for controller operations. |
| Deployment / notification-controller | warn; 0.25; `[network, podSecurity, images]` | restricted | Already hardened; API token required for controller operations. |
| Deployment / kustomize-controller | warn; 0.25; `[network, podSecurity, images]` | restricted | Already hardened; API token required for controller operations. |
| Deployment / konflate | warn; 0.25; `[network, podSecurity, images]` | restricted | Already hardened; API token already disabled. No redundant patch. |
| Deployment / helm-controller | warn; 0.25; `[network, podSecurity, images]` | restricted | Already hardened; API token required for controller operations. |
| Deployment / flux-operator | warn; 0.25; `[network, podSecurity, images]` | restricted | Already hardened; API token required for controller operations. |

Retained Jobs and scheduled execution still require their own startup and runtime
checks; a controller profile does not certify its generated Jobs.

## Other kguardian features (2026-10-28)

These are sanitized read-only results, not live hardening or an audit pass.
The profile table above quotes posture coverage and unknown dimensions; unknown
never means safe. Raw pod addresses, credentials and captures are not published.

| Feature used | Evidence and decision |
| --- | --- |
| Workload inventory, profiles and revision diffs | 6 controller profiles and diffs reviewed; revision details below. Observed churn is not necessarily configuration drift. |
| Namespace traffic | 0 records, 0 pods, 0 drops at this snapshot. Coverage is incomplete; a rolling count is not a before/after enforcement delta. |
| Image inventory | 6 running digests in the returned inventory; inventory truncated: false. Inventory is not a vulnerability verdict. |
| Vulnerabilities and exposure | kguardian's HIGH/CRITICAL query returned no findings because its image reports are absent. Separate Trivy findings appear below; kguardian exposure evaluation still needs ingested reports. This is not zero vulnerabilities. |
| SBOM and signer queries | Queried all 6 returned running digests: no SBOM reports, and signers unknown/not_checked. This is neither unsigned nor verified/trusted. No trusted issuer/subject is inferred. |
| Image-trust explanation | Evaluation unavailable: signature discovery is off. No ImageTrustPolicy is added without verified signer evidence and owner-selected trust roots. |
| Syscalls and profile generation | Representative `konflate` pod returned 87 observed x86_64 syscalls. Generation failed with the architecture error below; no custom profile is bound. |
| Seccomp denials | Profile counters have zero observed denials. No SeccompProfile CR exists; this is not proof of complete enforcement coverage or safe custom allowlists. |
| Compute findings | 6 victims evaluated; 0 findings; truncated=false, history_disabled=false. This is a bounded observation window. |
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
| Deployment / flux-operator | 27 → 28 | images | No peer changes |
| Deployment / helm-controller | 20 → 21 | network | 1 removed: ingress TCP/8080 |
| Deployment / konflate | 248 → 249 | network | 1 added: egress TCP/443 |
| Deployment / kustomize-controller | 17 → 18 | network | 1 removed: ingress TCP/8080 |
| Deployment / notification-controller | 17 → 18 | network | 1 removed: ingress TCP/9090 |
| Deployment / source-controller | 178 → 179 | network | 1 added: egress TCP/443 |

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

[Policy](../../kubernetes/apps/flux-system/networkpolicy.yaml) permits artifact/event
pod port 9090, external webhook 9292, internal operator UI 9080, vmagent metrics,
controller API/HTTPS and Konflate public rendering/image-verification HTTPS.
The operator's own selector grants controller egress and metrics independently of
the distribution controllers' `app.kubernetes.io/part-of: flux` label.
Konflate accepts the public gateway and AI Unified (`virtualmcpserver`, instance
`unified`) on 8080, but receives no API grant. Its additive chart policy is disabled.
There were no CronJobs/Jobs or configured movers in this namespace.

Audit source refreshes, chart installation/upgrades, remote bases, receiver hooks,
provider notifications, image verification, cold renders and AI Unified's MCP
call to Konflate `/mcp` on 8080. Zero namespace traffic records despite six
generated policies is a coverage gap.

## Local validation

`mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh` passed:
full Flux/Helm rendering, shellcheck, repository secret guardrails and all 25
regression tests. No live deployment or seven-day audit was performed.

## Trivy follow-up, 2026-10-28

The [image ledger](trivy-flux-system-images.csv) matches all six current image
digests to existing reports, 0–1 days old. Those reports contain no Critical/High
package findings; the [finding ledger](trivy-flux-system-findings.csv) is therefore
empty. This is bounded report evidence, not a new scan or a statement about other
severities, future images or runtime compromise. No image bump is justified by
these reports.

Read-only MCP inspection confirms the running Flux operator already sets
container `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `drop: [ALL]`,
`readOnlyRootFilesystem: true` and `RuntimeDefault`. AVD-KSV-0118 reports the empty
pod security context as allowing root, while the actual container overrides it.
The claimed root permission is not present. No redundant override or scanner
ignore is added.

Cluster-scoped reports must also be included: MCP confirms `cluster-admin` is
bound to Flux operator, Helm controller and Kustomize controller, and
`crd-controller-flux-system` is bound to the Flux controllers. AVD-KSV-0044/0046
and AVD-KSV-0041/0046 respectively remain controller privilege risks. This cluster's
GitOps controllers manage cluster resources and secrets, so deleting those grants
would stop reconciliation. Narrowing them requires a separate tenancy/service-
account design and permission tests, not a mechanical removal.

AVD-KSV-0046 on `flux-web-admin` remains relevant to the configured anonymous
internal UI's `flux-admin` group. The internal gateway is not an authentication
boundary. Changing its access model needs an explicit authentication/authorization
design and owner review; no functionality or permissions are silently removed.
The aggregate Flux view/edit/user roles are definitions whose warnings alone do
not prove a workload binding; do not count them as newly compromised workloads.
