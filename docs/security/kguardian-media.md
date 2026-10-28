# Media security draft

Local proposal on `harden/kguardian-media-security`, worktree
`.claude/worktrees/kguardian-media-security`, based on `5f33d7baf`.
The namespace CNP and Helm values are enforcement candidates. Merging would
change Flux-managed runtime behavior; neither a draft PR nor local validation
authorizes that. No live workload was changed or restarted for this review.

## Evidence and its limits

The 2026-10-28 review used namespace traffic, all 20 workload security profiles
and revision diffs, audit verdicts, compute findings, image inventory,
vulnerability listings and image-trust explanations. Recommendations were checked
against Helm values, live Pod templates and digest-pinned image configuration.
Raw observations stay outside this public repository.

Every posture below includes its reported coverage and unknown dimensions.
PSS levels are **upper bounds only**: kguardian evaluated nine of eighteen checks
and does not establish restricted compliance. A missing recommendation is not
an all-clear. No custom SeccompProfile currently enforces the observed syscall
set; explicit `RuntimeDefault` is distinct from such a profile.

No `unshippedExecutable` or `securityContextRegression` item was reported, but
the executable inventory and security-context baselines needed to evaluate them
are missing. Both remain **unknown**. The CronJob diffs lose container context
after completion; those null values do not prove a configuration regression.
Radarr added `rmdir` to its observed syscalls; Unpackerr added `readv`, `recvmsg`
and `setitimer`. These changes must remain in the seccomp review evidence.

Audit verdicts were empty without a completed audit window. Image-trust
evaluation is unavailable, and the zero HIGH/CRITICAL vulnerability results omit
images without reports: neither result is a pass. Compute findings reported
FileFlows CPU throttling around 32% in its five-minute finding. A longer
54-sample window showed CPU averaging 1.366 cores, peaking at 1.985 cores,
and a throttled ratio of 0.52, without OOM events. Node contention was not loaded
and returned no pairs, so there is no causal blame evidence. Preserve the
intentional two-core limit; defer sizing to a matched transcode/node-load test.

## Recommendation decisions

“Applied” means added to this local draft. “Already” means present in the current
configuration. “Exempt” preserves a verified startup requirement; “deferred”
needs further compatibility or coverage evidence.

| Workload | Posture; coverage; unknown dimensions | PSS upper bound | Decision |
| --- | --- | --- | --- |
| Deployment/bazarr | warn; 25%; network, podSecurity, images | restricted | Already: non-root identity, escalation disabled, drop ALL, read-only root and RuntimeDefault. No additional patch. |
| Deployment/bitmagnet | warn; 25%; network, podSecurity, images | restricted | Already: existing pod and container hardening; no additional patch. |
| Deployment/armiger-connector | warn; 25%; network, podSecurity, images | restricted | Already: app and bootstrap init container use non-root identity, escalation disabled, drop ALL, read-only root and RuntimeDefault. Downloaded bootstrap/model content remains a supply-chain review item. |
| CronJob/bitmagnet-prune | warn; 25%; network, podSecurity, images | unknown | Already in Helm: non-root, RuntimeDefault and hardened app/select init contexts. Deferred runtime conclusion: no current container context or recommendation. |
| Deployment/brrpolice | warn; 50%; network, images | baseline | Applied: RuntimeDefault. Already: non-root, escalation disabled, drop ALL, read-only root. |
| Deployment/cleanrr | warn; 50%; network, images | baseline | Applied: RuntimeDefault. Already: non-root, escalation disabled, drop ALL, read-only root. |
| Deployment/deduparr | warn; 50%; network, images | baseline | Applied: RuntimeDefault. Already: non-root, escalation disabled, drop ALL, read-only root. |
| Deployment/fileflows | risk; 50%; network, images | baseline | Applied: escalation disabled and read-only root for the init container, with mkdir/chown limited to shared mounts. Already: RuntimeDefault. Exempt: root startup and ownership capabilities. Deferred: app escalation/capability changes and read-only root. |
| Deployment/flaresolverr | warn; 50%; network, images | baseline | Applied: RuntimeDefault, escalation disabled and drop ALL. Deferred: numeric non-root identity and read-only root; image uses a named USER and patches Chromium's driver at runtime. |
| Deployment/jellyfin | warn; 25%; network, podSecurity, images | restricted | Already: non-root, escalation disabled, drop ALL and RuntimeDefault. Deferred: read-only root until writable paths and GPU playback/transcoding are exercised. |
| Deployment/jellystat | warn; 50%; network, images | baseline | Applied: RuntimeDefault, escalation disabled and drop ALL. Deferred: non-root UID and read-only root until npm/application writes are mapped and tested. |
| Deployment/prowlarr | warn; 25%; network, podSecurity, images | restricted | Already: existing pod/container hardening. No additional patch. |
| Deployment/qbittorrent | warn; 50%; network, images | baseline | Applied: RuntimeDefault. Already: non-root, escalation disabled, drop ALL, read-only root. |
| Deployment/qui | warn; 25%; network, podSecurity, images | restricted | Already: existing pod/container hardening. No additional patch. |
| Deployment/radarr | warn; 25%; network, podSecurity, images | restricted | Already: existing pod/container hardening. No additional patch; retain new syscall evidence. |
| CronJob/recyclarr | warn; 25%; network, podSecurity, images | unknown | Already in Helm: non-root, RuntimeDefault and hardened container context. Deferred runtime conclusion: completed Job context is stale. |
| StatefulSet/seerr | warn; 25%; network, podSecurity, images | restricted | Applied: native chart `serviceAccount.automount: false`. Already: non-root, RuntimeDefault, escalation disabled and drop ALL. Deferred: read-only root. |
| Deployment/sonarr | warn; 25%; network, podSecurity, images | restricted | Already: existing pod/container hardening. No additional patch. |
| Deployment/unpackerr | warn; 25%; network, podSecurity, images | restricted | Already: existing pod/container hardening. No additional patch; retain new syscall evidence. |
| Deployment/wizarr | warn; 50%; network, images | baseline | Applied: RuntimeDefault. Already: escalation disabled. Exempt: root ownership setup and UID/GID-changing capabilities; defer non-root and read-only root pending a separate startup/PVC trial. |

RuntimeDefault was already the repository's declared Talos kubelet default in
`talos/cluster.yaml.j2`; making it explicit records the intended workload policy.
This does not prove the active kubelet configuration or application compatibility.

The exact Flaresolverr image records source revision
`93125d6ea6956906c8dfa29e169b2dc67d993dc3`, uses a named non-root USER, and
[disables Chromium's setuid sandbox](https://github.com/FlareSolverr/FlareSolverr/blob/93125d6ea6956906c8dfa29e169b2dc67d993dc3/src/utils.py#L128).
The proposed capability/escalation settings preserve that startup mechanism;
`runAsNonRoot: true` alone would fail kubelet verification of a named image USER.
Do not guess its numeric UID. The writable driver and browser paths are retained.

The Jellystat image starts its [entry script](https://github.com/CyferShepard/Jellystat/blob/1.1.12/entry.sh),
which reads configured secret files and starts npm without an ownership or
privilege-switching step. The draft removes capability and executable privilege
gains while retaining its current user and writable filesystem.
Wizarr's [entrypoint](https://github.com/wizarrrr/wizarr/blob/v2026.10.1/docker-entrypoint.sh)
creates users/groups, changes ownership and runs `su-exec`; those permissions
remain. FileFlows retains the documented apt/group setup, GPU groups, root init
ownership work and writable transcode scratch. No s6/root startup path was
converted to generic non-root/read-only execution.

Seerr's pinned chart renders the setting on its ServiceAccount. It has no
Kubernetes API integration. **Existing pods retain their mounted token until
recreated**; verify the replacement pod after an owner-approved rollout.

## Validation and approval gates

Local validation passed: `mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh`
completed the full Flux/Helm render, shellcheck, secret guardrails and 25 regression
tests. Pinned app-template 5.3.0 and Seerr 3.10.1 renders verified all 20 workload
and CronJob pod contexts, Seerr's ServiceAccount, the FileFlows root/GPU exception,
and unchanged image/resource values. Static success is not runtime validation.

Before enforcement, require the owner's approval, at least seven days of audit
coverage, and evidence for every legitimate WouldDeny. Include daily prune and
Recyclarr, nightly Kopiur movers, startup packages, invitations, playback,
downloads, provider/webhook paths and DNS replies. Recheck all CNP/native-policy
unions and preserve the deny-apiserver overlay and backup exceptions. Resolve
the unexplained earlier Jellyfin LAN UDP path before tightening it. Obtain
executable inventory and security-context baselines before claiming drift is
clear. Apply custom seccomp or image-trust policies only as a separate reviewed
stage; their generated artifacts are not authorizations to enforce them.

## Other kguardian features (2026-10-28)

These are sanitized read-only results, not live hardening or an audit pass.
The profile table above quotes posture coverage and unknown dimensions; unknown
never means safe. Raw pod addresses, credentials and captures are not published.

| Feature used | Evidence and decision |
| --- | --- |
| Workload inventory, profiles and revision diffs | 20 controller profiles and diffs reviewed; revision details below. Observed churn is not necessarily configuration drift. |
| Namespace traffic | 4992 records, 2 pods, 121 drops at this snapshot. Coverage is incomplete; a rolling count is not a before/after enforcement delta. |
| Image inventory | 19 running digests in the returned inventory; inventory truncated: false. Inventory is not a vulnerability verdict. |
| Vulnerabilities and exposure | kguardian's HIGH/CRITICAL query returned no findings because its image reports are absent. Separate Trivy findings appear below; kguardian exposure evaluation still needs ingested reports. This is not zero vulnerabilities. |
| SBOM and signer queries | Queried all 19 returned running digests: no SBOM reports, and signers unknown/not_checked. This is neither unsigned nor verified/trusted. No trusted issuer/subject is inferred. |
| Image-trust explanation | Evaluation unavailable: signature discovery is off. No ImageTrustPolicy is added without verified signer evidence and owner-selected trust roots. |
| Syscalls and profile generation | Representative `jellyfin` pod returned 102 observed x86_64 syscalls. Generation failed with the architecture error below; no custom profile is bound. |
| Seccomp denials | Profile counters have zero observed denials. No SeccompProfile CR exists; this is not proof of complete enforcement coverage or safe custom allowlists. |
| Compute findings | 18 victims evaluated; 1 findings; truncated=false, history_disabled=false. This is a bounded observation window. |
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
| CronJob / bitmagnet-prune | 6 → 7 | images, podSecurity | No peer changes |
| CronJob / recyclarr | 53 → 54 | images, podSecurity | No peer changes |
| Deployment / armiger-connector | 31 → 32 | images | No peer changes |
| Deployment / bazarr | 53 → 54 | network | 1 added: egress TCP/443 |
| Deployment / bitmagnet | 373 → 374 | network | 50 added: egress TCP/10313, egress TCP/11082, egress TCP/11782, egress TCP/12619, egress TCP/13122, egress TCP/14227, egress TCP/14395, egress TCP/14961, egress TCP/15035, egress TCP/15066, egress TCP/15188, egress TCP/15371, egress TCP/15500, egress TCP/15654, egress TCP/15690, egress TCP/15703, egress TCP/15794, egress TCP/15906, egress TCP/15961, egress TCP/16006, egress TCP/16047, egress TCP/16083, egress TCP/16168, egress TCP/16179, egress TCP/16200, egress TCP/16260, egress TCP/16371, egress TCP/16396, egress TCP/16411, egress TCP/16457, egress TCP/16536, egress TCP/16598, egress TCP/16687, egress TCP/16762, egress TCP/16861, egress TCP/16881, egress TCP/17007, egress TCP/17047, egress TCP/17161, egress TCP/17205, egress TCP/17214, egress TCP/17261, egress TCP/17497, egress TCP/17668, egress TCP/17729, egress TCP/17730, egress TCP/17803, egress TCP/17913, egress TCP/17943; 50 removed: egress TCP/10181, egress TCP/10223, egress TCP/10238, egress TCP/1024, egress TCP/10305, egress TCP/10322, egress TCP/1040, egress TCP/10456, egress TCP/10586, egress TCP/10653, egress TCP/11000, egress TCP/11061, egress TCP/11169, egress TCP/11250, egress TCP/11616, egress TCP/11637, egress TCP/12072, egress TCP/12093, egress TCP/12167, egress TCP/12345, egress TCP/12380, egress TCP/12445, egress TCP/12529, egress TCP/12588, egress TCP/12613, egress TCP/12736, egress TCP/12819, egress TCP/12835, egress TCP/12947, egress TCP/13074, egress TCP/13149, egress TCP/13165, egress TCP/13216, egress TCP/13227, egress TCP/13416, egress TCP/13777, egress TCP/13797, egress TCP/13854, egress TCP/13907, egress TCP/13940, egress TCP/14138, egress TCP/14247, egress TCP/14495, egress TCP/14502, egress TCP/14576, egress TCP/14604, egress TCP/14616, egress TCP/14628, egress TCP/14683, egress TCP/14767 |
| Deployment / brrpolice | 13 → 14 | network | 1 removed: ingress TCP/80 |
| Deployment / cleanrr | 12 → 13 | network | 1 added: ingress TCP/80 |
| Deployment / deduparr | 5 → 6 | network | 1 added: ingress TCP/80 |
| Deployment / fileflows | 71 → 72 | network | 1 added: egress TCP/80 |
| Deployment / flaresolverr | 271 → 272 | network | 1 removed: egress TCP/443 |
| Deployment / jellyfin | 133 → 134 | network | 1 removed: egress TCP/443 |
| Deployment / jellystat | 13 → 14 | network | 1 removed: egress TCP/443 |
| Deployment / prowlarr | 21 → 22 | images | No peer changes |
| Deployment / qbittorrent | 2595 → 2596 | network | 50 added: egress TCP/10220, egress TCP/10266, egress TCP/10645, egress TCP/10840, egress TCP/11097, egress TCP/11532, egress TCP/12635, egress TCP/12636, egress TCP/12637, egress TCP/12638, egress TCP/1264, egress TCP/12641, egress TCP/12642, egress TCP/12643, egress TCP/12644, egress TCP/12649, egress TCP/12650, egress TCP/12653, egress TCP/12656, egress TCP/12659, egress TCP/1266, egress TCP/12663, egress TCP/12664, egress TCP/12665, egress TCP/12666, egress TCP/12667, egress TCP/12668, egress TCP/12670, egress TCP/12675, egress TCP/12676, egress TCP/12678, egress TCP/12679, egress TCP/1268, egress TCP/12681, egress TCP/12687, egress TCP/1269, egress TCP/12690, egress TCP/12691, egress TCP/12695, egress TCP/12700, egress TCP/12713, egress TCP/12715, egress TCP/12719; 50 removed: egress TCP/10041, egress TCP/10046, egress TCP/10094, egress TCP/10096, egress TCP/10124, egress TCP/10140, egress TCP/10177, egress TCP/10201, egress TCP/1021, egress TCP/10228, egress TCP/1024, egress TCP/10306, egress TCP/10418, egress TCP/1042, egress TCP/10431, egress TCP/10472, egress TCP/10550, egress TCP/10599, egress TCP/1065, egress TCP/10861, egress TCP/10863, egress TCP/10886, egress TCP/10902, egress TCP/11023, egress TCP/11059, egress TCP/11060, egress TCP/11063, egress TCP/1109, egress TCP/11281, egress TCP/11460, egress TCP/11516, egress TCP/11640, egress TCP/11655, egress TCP/11667, egress TCP/11731, egress TCP/1179, egress TCP/11829, egress TCP/11836, egress TCP/1197, egress TCP/12021, egress TCP/12028, egress TCP/12080, egress TCP/12167, egress TCP/12209, egress TCP/12244, egress TCP/12342, egress TCP/1239, egress TCP/12409, egress TCP/12569 |
| Deployment / qui | 82 → 83 | network | 3 added: egress TCP/443 |
| Deployment / radarr | 95 → 96 | syscalls | No peer changes |
| Deployment / sonarr | 69 → 70 | images | No peer changes |
| Deployment / unpackerr | 19 → 20 | network, syscalls | 1 added: egress UDP/53 |
| Deployment / wizarr | 16 → 17 | network | 1 removed: egress TCP/8096 |
| StatefulSet / seerr | 60 → 61 | network | 1 added: egress TCP/443 |

FileFlows compute follow-up: the initial five-minute finding reported 32% CPU
throttling at its two-core limit. `get_pod_compute` then returned 54 minute samples
from 11:52:28 to 12:45:28 UTC, average CPU 1.37 cores, maximum 1.98 and aggregate
throttled ratio 0.52. No OOM kill was reported. Node contention tracing was not
loaded and `get_node_contention` returned no pairs: no noisy-neighbor attribution
is possible. Keep the limit pending a matched transcode/node-load test; increasing
it automatically could harm other services. This resource decision is separate
from the FileFlows root-startup exception.

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

[Policy](../../kubernetes/apps/media/networkpolicy.yaml) permits named Arr/download,
subtitle, invitation, playback, widget and exporter clients. Only Jellyfin 8096,
Seerr 5055 and Wizarr 5690 accept the public gateway. Existing Flaresolverr rules
remain narrow. SQL clients use poolers; Bitmagnet/prune require the session pooler.
Seerr/Jellystat gateway hairpins use 10443. Unpackerr uses current port **5656**,
including Hermes access; historical 16373 is stale.

Only qBittorrent and main Bitmagnet get unrestricted world P2P egress. FileFlows'
root-startup package retrieval and Armiger's package/model downloads remain;
root-startup exceptions remain; context changes are described above. Recyclarr/prune schedules and Kopiur movers have
separate grants. Audit these rare paths, provider redirects, notifications and
backups, and investigate Jellyfin's unexplained LAN UDP/65001 before enforcement.
Historical qBitrr and migration Jobs do not justify grants to future workloads.

## Trivy follow-up (2026-10-28)

Trivy Operator reports were joined to current controller/Pod ownership and checked
against image digests. The complete report inventory was also checked against
current regular/init container spec digests, including old-Job reports where the
exact image is still configured. No additional media image was found. The separate [image ledger](trivy-media-images.csv) records
all 21 image digests, report times/ages, publisher status and remaining blockers;
join its `image_id` with the [complete finding ledger](trivy-media-findings.csv).
The latter retains all 2,249 Critical/High image/package/version findings after
deduplicating repeated container/target copies: 78 Critical and 2,171 High.
Of these, 1,260 have a package fix version reported. Blank fixed-version fields
mean the report supplied no fix, not that remediation is impossible. Multiple
fixed versions are retained as reported; these counts are not unique CVE counts.

The reports are current/recent image scans (zero or one day old), but several
configuration reports date to September. None is a scan of this draft. Same-tag
publisher registry checks found no changed effective image content. In particular,
the PostgreSQL `18.6-standard-trixie` index resolves to the existing pinned amd64
manifest; replacing the pin with its index would not patch the prune init image.
Release checks found the same deployed versions for the principal applications,
including Flaresolverr, Seerr, Wizarr, Jellystat and Bazarr+. Where a release
catalog was unavailable or empty, the ledger states that limitation.
No image pin is changed and **no image finding is claimed resolved**. A fixed
package version is not evidence that a compatible publisher image contains it.

The raw Trivy reports contain findings that kguardian currently returns as
`noVulnerabilityData`; its missing supply-chain ingestion explains why its earlier
empty vulnerability result cannot be used as a clean scan. Konflate 0.6.6 renders
Flux diffs and checks image existence; it does not run Trivy or publish a Trivy
scan artifact. No scan was forced, no image executed, and no suppression added.

| Current High configuration finding | Draft decision |
| --- | --- |
| FileFlows init: AVD-KSV-0014 | Add read-only root after restricting initialization to `/app/Data` and `/temp`, the shared mounts. The former `/app/Logs` and `/app/ManuallyAdded` directories existed only in the init container's private filesystem and did not initialize the app. UID 0, capabilities and both shared-volume ownership changes stay intact. |
| FileFlows app: AVD-KSV-0014 | Remains an explicit exception: package/user/group startup and transcode scratch require writable root. No startup package upgrade is added. |
| Flaresolverr and Jellystat: AVD-KSV-0118 | Existing draft contexts replace the defaults; confirmation requires a new rendered/live audit report. |
| Flaresolverr, Jellystat, Jellyfin, Seerr: AVD-KSV-0014 | Remain deferred until browser/driver, npm, GPU and application writes are mapped and tested. Do not substitute a read-only setting merely to clear a scanner row. |
| Wizarr: AVD-KSV-0118 and AVD-KSV-0014 | Preserve documented root user/group and ownership startup; the earlier RuntimeDefault addition does not remove that exception. |

Old ReplicaSet findings were excluded from the current-workload decisions.
The retained `volsync-copy-data` Job completed in December 2025; its default-context
and writable-root findings are historical, not a running migration. Nothing was
deleted. No current Critical/High namespace RBAC report was found in the collected
inventory. The proposed context changes still require owner-approved runtime
validation and a fresh report; they do not remediate the image CVEs in the ledger.

The follow-up passed the required `validate-pr.sh` checks and a targeted FileFlows
Helm render. Assertions verified that only the init command and read-only field
changed; shared-volume ownership, main-container context and GPU groups remain
unchanged. The ledger retains every Critical/High finding and reported fixed
version from all matching current spec/runtime image reports.
