# Kritika security hardening decisions

Branch `harden/kguardian-kritika-security`; worktree
`.claude/worktrees/kguardian-kritika-security`, based on main `5f33d7baf`.
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
| Deployment / kritika | warn; 0.25; `[network, podSecurity, images]` | restricted | Already hardened; API token required to manage runner Jobs. Preserve gVisor runner isolation. |

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
| Syscalls and profile generation | Representative `kritika` pod returned 81 observed x86_64 syscalls. Generation failed with the architecture error below; no custom profile is bound. |
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
| Deployment / kritika | 1322 → 1323 | network | 10 removed: ingress TCP/8082 |

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

[Policy](../../kubernetes/apps/kritika/networkpolicy.yaml) replaces chart-generated
server allow-all ingress and runner `ingress: [{}]`. Server paths remain Envoy
8080, vmagent 8081, runner gateway 8082, API, model gateway 4000, DB and HTTPS.
Runners have only service-name DNS, DB and gateway egress; no internet/API grant.
Host ingress for kubelet probes selects only the server; runners have no probes.
The runner's L7 policy is a separate object, so its rejection does not remove the
namespace default-deny or server allowlist. The native DB/gateway-only fallback
has no DNS and confines runners if Cilium rejects their L7 rule. gVisor and chart
admission controls remain enabled. The public
HTTPRoute still limits access to `/hooks/`.

Both `pgbouncer-rw` and `pgbouncer-session` rejected each startup parameter used
by pinned Kritika 0.0.109: `tcp_keepalives_idle`, `tcp_keepalives_interval`,
`tcp_keepalives_count`, and `statement_timeout`. Baseline startup reached an
authentication request; each added parameter produced SQLSTATE `08P01`.
No password, authenticated login or SQL was used. Its
[pool config](https://github.com/home-operations/kritika/blob/0.0.109/internal/store/store.go),
[LISTEN connection](https://github.com/home-operations/kritika/blob/0.0.109/internal/store/notify.go)
and [session locks](https://github.com/home-operations/kritika/blob/0.0.109/internal/store/leader.go)
confirm the direct-DB exception is still needed. Transaction pooling also lacks
[LISTEN/session-lock semantics](https://www.pgbouncer.org/features.html).
A session-pooler migration needs parameter/timeout handling, migration, notification
and lock-lifetime tests. The protocol probe did not test the same-node VIP issue.

All six read-only MCP tools were used: `list_findings`, `common_findings`,
`get_pull`, `get_review`, `list_rules`, `list_followups`. Four of 14 historical
Kubernetes security findings remained marked open: the old Cilium-auto concern,
already-fixed Satisfactory exposure, an informational plugin change and a removed
workload. Current manifests were checked; nothing was dismissed remotely.
Historical reviews do not approve this proposal.

Git/gh/curl, repository/OCI fetches, Aqua tools and repository commands use gateway
8082; indexing stages through DB, and embedding/search use server-to-model 4000.
The filesystem cache needs no S3 rule, and cache HTTP upgrades do not require
server TCP/80. Review/polling/publication/follow-up paths use existing HTTPS/DB.
The configured flate tool may need registries missing from Kritika's own host
allowlist: `quay.io`, `mirror.gcr.io`, `gsoci.azurecr.io`,
`registry.erwanleboucher.dev`. Verify attempted hosts and auth/blob redirects in
refused-host analytics before adding entries. A bounded 1,705-line server log
sample found no refusals; this is not proof those registries were fetched.
MCP omits transcripts, raw output, usage and refused-host analytics: use the
existing dashboard/queue/metrics for those checks. Disclose unavailable tools,
compare reviewed SHAs and reject incomplete/skipped review as approval evidence.

## Local validation

`mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh` passed:
full Flux/Helm rendering, shellcheck, repository secret guardrails and all 25
regression tests. No live deployment or seven-day audit was performed.

## Trivy follow-up, 2026-10-28

The [image ledger](trivy-kritika-images.csv) records server and tools-runner
digests separately. Read-only MCP queries confirm that the server's current
digest matches its 2026-10-27 report. The
[finding ledger](trivy-kritika-findings.csv) contains its one High finding:
CVE-2026-84445 in `google.golang.org/grpc` v1.83.1. The report lists fixes in
1.82.2, 1.83.2 and two development branches; use a compatible patched release,
not a downgrade based only on that unordered list.

Publisher release 0.0.110 was checked, but its
[tagged source](https://github.com/home-operations/kritika/blob/d8327f8ba9efd5130d3f45a0c709bf1cc68ccd6e/go.mod)
still selects gRPC v1.83.1. Updating only the image tag would not demonstrate a
fix. Keep the finding unresolved until a publisher patch and matching scan exist.
The tools-runner digest has no matching report and remains **unknown**; the
server report cannot establish the runner's package contents.
Its runtime digest is recorded as `observed_image_digest`; `report_digest: none`
keeps the missing scanner evidence distinct from the observed image identity.

Current namespace config/RBAC reports contain no failed Critical/High checks.
MCP confirms the namespace RoleBinding; no service-account ClusterRoleBinding
was found. Preserve the existing server Job/API permissions and isolated runner
context. No scanner ignores, custom images or live changes are introduced.
