# Web3 security draft

Local proposal on `harden/kguardian-web3-security`, based on `5f33d7baf`.
The namespace CNP and Helm values are enforcement candidates. Neither local
validation nor a draft PR authorizes deployment. No workload was changed,
restarted or scaled for this review.

## Evidence and its limits

The 2026-10-28 review used the kguardian features tabled below. Recommendations
were checked against Helm/ResourceSet templates and digest-pinned monerod image
metadata. Raw observations (pod addresses, credentials, captures) stay outside
this public repository. Unknown never means safe.

PSS levels below are **upper bounds only**, never a restricted-compliance claim:
kguardian evaluates only nine of eighteen checks. No custom SeccompProfile
currently enforces the observed syscall set. RuntimeDefault is a different
runtime profile and does not resolve that missing-CR finding.

No dangerous drift item was reported, but `unshippedExecutable` is not evaluated
without executable inventory and `securityContextRegression` lacks a baseline.
Both remain **unknown**; investigate any future positive finding before advancing
enforcement. The miner revision changes from null to known container context
reflect observation/scale lifecycle; they are not proof of a privilege regression
or complete coverage. Keep the crypto-miner guard history in scope.

## Recommendation decisions

“Applied” means added to this local draft; “already” means present in current
configuration. Deferred settings need a separate compatibility trial.

| Workload | Posture; coverage; unknown dimensions | PSS upper bound | Decision |
| --- | --- | --- | --- |
| Deployment/monerod | warn; 50%; network, images | baseline | Applied: RuntimeDefault and escalation disabled. Exempt: root startup and ownership/UID/GID capabilities. Deferred: non-root enforcement, drop ALL and read-only root. |
| Deployment/p2pool | warn; 50%; network, images | baseline | Applied: RuntimeDefault and read-only root after checking pinned startup/data paths. Already: non-root, escalation disabled and drop ALL. Preserve writable data/API/tmp mounts; runtime verification remains required. |
| Deployment/dashboard | warn; 50%; network, images | baseline | Applied: pod RuntimeDefault for both Python and nginx containers. Already: non-root, escalation disabled, drop ALL and read-only root; shared PVC group stays unchanged. |
| Deployment/guard | warn; 25%; network, podSecurity, images | restricted | Already: non-root, RuntimeDefault, escalation disabled, drop ALL and read-only root. No additional patch. |
| Deployment/xmrig-control-1, -2, -3 | warn; 50%; network, images | baseline | Applied: RuntimeDefault through the shared ResourceSet Helm values. Already: non-root, escalation disabled, drop ALL and read-only root. Preserve no-huge-pages and thermal/solar scaling. |

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
startup permissions. Static rendering and an empty query are not runtime validation.

Before merging the enforcing CNPs: refresh live settings and workloads; obtain
owner approval for the audit deployment; collect at least seven complete days of
audit evidence covering miner scale-up/down (all three miners were at zero and no
scale-up was induced), guard and monitoring failures, reconnects, P2P churn and
dashboard fallbacks; fix every legitimate WouldDeny with evidence; validate the
unsupported Cilium dimensions (see feature prerequisites); then obtain explicit
enforcement approval. Watch drop deltas and app health after an approved rollout.
Also before enforcement:

- Resolve XMRig donation destinations/ports before any Internet grant or
  owner-approved command change: the command does not explicitly disable
  donation, and current evidence does not justify a guessed grant.
- Verify LAN LoadBalancer source ranges and observed source identity.
- Preserve the deny-apiserver component.
- Collect executable inventory and security-context baselines before treating
  drift as clear.
- Custom seccomp and image-trust artifacts need their own review and approval.

## Other kguardian features (2026-10-28)

Sanitized read-only results, not live hardening or an audit pass.

| Feature used | Evidence and decision |
| --- | --- |
| Workload inventory, profiles and revision diffs | 7 controller profiles and diffs reviewed; revision details below. Observed churn is not necessarily configuration drift. |
| Namespace traffic | 3 records, 1 pod, 0 drops at this snapshot. Coverage is incomplete; a rolling count is not a before/after enforcement delta. |
| Image inventory | 6 running digests in the returned inventory; inventory truncated: false. Inventory is not a vulnerability verdict. |
| Vulnerabilities and exposure | kguardian's HIGH/CRITICAL query returned no findings because its image reports are absent. Separate Trivy findings appear below; kguardian exposure evaluation still needs ingested reports. This is not zero vulnerabilities. |
| SBOM and signer queries | Queried all 6 returned running digests: no SBOM reports, and signers unknown/not_checked. This is neither unsigned nor verified/trusted. No trusted issuer/subject is inferred. |
| Image-trust explanation | Evaluation unavailable: signature discovery is off. No ImageTrustPolicy is added without verified signer evidence and owner-selected trust roots. |
| Syscalls and profile generation | Representative `monerod` pod returned 108 observed x86_64 syscalls. Generation failed with the architecture error below; no custom profile is bound. |
| Seccomp denials | Profile counters have zero observed denials. No SeccompProfile CR exists; this is not proof of complete enforcement coverage or safe custom allowlists. |
| Compute findings | 7 victims evaluated; 0 findings; truncated=false, history_disabled=false. This bounded window does not justify changing mining quotas, thermal thresholds or scale decisions. |
| Audit verdicts | WouldDeny query empty; live inventory contains no AuditNetworkPolicy/AuditClusterNetworkPolicy CRs. No audit has started or passed. |

The MCP `generate_seccomp_profile` call returned `unrecognized architecture —
no seccomp arch mapping (supported: x86_64, aarch64)`, even though
`get_pod_syscalls` reports x86_64 for the same pod. Treat this as an export blocker.
Keep RuntimeDefault; do not replace it with an unverified allowlist or LOG profile.
A workload aggregate can include older pods and differs from this single-pod set.
Resolve the exporter and capture startup, Jobs and rare paths before proposing
a custom profile; no generated syscall set is claimed complete over seven days.

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

### Feature prerequisites

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

## Network policy

[Policy](../../kubernetes/apps/web3/networkpolicy.yaml) separates monerod, p2pool,
miners, dashboard and guard. Monerod/p2pool retain variable-port world P2P egress;
miners get stratum 3333 and dashboard API ingress 42000. Guard reaches vmauth 8427
and exposes metrics only. LoadBalancer listener grants still depend on existing
`${LAN_CIDR}` source ranges and preserved client identity; `world` is not LAN-only.

## Trivy follow-up (2026-10-28)

The [image ledger](trivy-web3-images.csv) records five image contents (six reported
digests), scan times/ages, publisher checks and blockers; join `image_id` with the
[complete finding ledger](trivy-web3-findings.csv). After deduplicating the shared
Python/miner images and repeated target copies, the reports contain 355
image/package/version findings: 12 Critical and 343 High. Sixteen have a package
fix version reported; none is claimed resolved. Blank fixed versions mean the
report supplied none. No image version or digest changes: publisher tag content
for the configured amd64 platform is unchanged, and a fixed dependency version
alone is not a verified patched publisher image. The latest stable Monero and
P2Pool releases match this draft; the P2Pool beta is not selected as a
speculative security fix.

The reports are zero or one day old, but they are not scans of the proposed
contexts. All regular/init container specs and runtime digests were matched
against the complete report inventory, including reports from older owners with
an identical digest. This includes the three scaled-to-zero miners: their
retained exact-digest report from October 27 contains 12 Critical and 289 High
findings, and is retained as configured-image evidence rather than proof of
current execution. Python/nginx reports match the configured index digests.
Their live `imageID` values name different OCI indexes, but reading both indexes
and their amd64 children confirms identical manifest, config and layer digests
on the cluster's amd64 nodes; only ppc64le descriptors differ. The older guard
report for the equivalent Python index is included and adds no unique finding.
This is platform-content equivalence, not evidence of drift or a fresh scan. No
scan was forced, image executed, runtime changed or suppression added.

P2Pool's High AVD-KSV-0014 finding is addressed in the draft by making its root
filesystem read-only. The exact
[image source](https://github.com/sethforprivacy/p2pool-docker/blob/21106ba39adefd53dbf9f9ffe316f1f3b836b5e6/Dockerfile)
sets a non-root user and `/home/p2pool` working directory, already a writable PVC.
The pinned application's
[startup check](https://github.com/SChernykh/p2pool/blob/abc833598f80d7a0345b6020ced90f19957d7a7c/src/params.cpp#L147)
uses the working directory or home fallback; cache, logs and saved peers use that
data directory. API output, including temporary files before rename, uses the
configured `/var/lib/p2pool/api` writable volume; `/tmp` keeps its emptyDir.
The command, UID, groups, volumes, ports, resource settings and miner safeguards
are unchanged. Cold start, peer/cache persistence and dashboard API writes still
need verification after an approved rollout.

Monerod's High AVD-KSV-0118 is addressed by the earlier explicit container context;
a fresh audit must confirm the result. Its AVD-KSV-0014 remains a documented
writable-root/startup exception. Older ReplicaSet reports are historical; no
current Critical/High namespace RBAC finding appeared in the collected inventory.
Filesystem restrictions do not remediate the image findings recorded in the ledger.

The follow-up passed the required `validate-pr.sh` checks and a targeted P2Pool
Helm render. Assertions verified that only the read-only field changed, with the
UID, dropped capabilities and writable mounts preserved.
