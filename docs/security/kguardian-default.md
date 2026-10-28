# default: kguardian hardening draft

Evidence collected on 2026-10-28. This is a reviewable GitOps proposal: no cluster configuration was applied or reconciled. Network restrictions and workload contexts require owner approval before rollout. The network draft still requires at least seven days of representative audit evidence, including scheduled jobs and backups.

The table records the **current observed posture**, not a prediction after these changes. Coverage is kguardian overall coverage; unknown dimensions are listed explicitly. PSS values are **upper bounds only**, with 9 of 18 checks evaluated where data exists. A `restricted` upper bound does not establish restricted compliance. `Apply` means proposed in this branch, `already` means retained, and exemptions/deferred items remain unresolved restrictions.

| Workload | Posture / coverage / unknown dimensions | PSS upper bound | Recommendation decision and evidence |
| --- | --- | --- | --- |
| CronJob/nextcloud-cron | warn / 50% / network, images | baseline | Apply RuntimeDefault, no privilege escalation and drop ALL. Pinned chart runs cron.sh directly as existing uid/gid 33; it does not run the root image entrypoint. |
| CronJob/satisfactory-restart | warn / 25% / network, podSecurity, images | unknown | Already configured in the CronJob manifest: uid/gid 65534, non-root, RuntimeDefault, no privilege escalation and drop ALL. Broker pod/container coverage is missing; no compliance conclusion. |
| Deployment/spoolman | warn / 50% / network, images | baseline | Apply RuntimeDefault only. Defer non-root, no privilege escalation and drop ALL until image startup and writable-directory ownership are verified. |
| Deployment/searxng | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/satisfactory | warn / 50% / network, images | baseline | Apply RuntimeDefault; preserve steam uid/gid 1000 and existing hardening for the main and mod initialization containers. |
| Deployment/picoshare | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain existing non-root identity and container controls. |
| Deployment/obico-server | risk / 50% / network, images | baseline | Apply RuntimeDefault; add non-root, no privilege escalation and drop ALL to server, migrate and tasks using pinned chart uid/gid 65534. collectstatic retains its explicit root uid/gid 0 and capabilities for shared static assets; only disable privilege escalation there. |
| Deployment/obico-ml-api | warn / 50% / network, images | baseline | Apply RuntimeDefault, non-root, no privilege escalation and drop ALL; pinned chart already supplies uid/gid 65534 and directly starts gunicorn. |
| Deployment/notify-push | warn / 50% / network, images | baseline | Apply RuntimeDefault only. Defer identity and capability restrictions until image entrypoint and writable-path behavior are verified. |
| Deployment/nextcloud | risk / 50% / network, images | baseline | Apply RuntimeDefault through nextcloud.podSecurityContext. Preserve root app startup: the documented hook installs packages and the entrypoint changes ownership/drops privileges. Defer app/nginx non-root, no privilege escalation and drop ALL pending startup testing. |
| Deployment/karakeep-meilisearch | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain existing non-root identity and container controls. |
| Deployment/moonraker-obico | risk / 50% / network, images | privileged | Retain existing RuntimeDefault and container hardening. Exemption: hostNetwork is required for the configured printer connection; no host-network change. |
| Deployment/karakeep-chrome | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain existing container controls, gVisor and documented browser startup flags. |
| Deployment/karakeep | warn / 50% / network, images | baseline | Apply RuntimeDefault. Exemption: the documented s6-overlay-suexec startup needs setuid and chown before dropping to uid 1000; defer no privilege escalation/drop ALL for this container. |
| Deployment/it-tools | warn / 50% / network, images | baseline | Apply RuntimeDefault and runAsNonRoot using the existing uid/gid 1000; retain container controls. |
| Deployment/homepage | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/ghostfolio | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain existing non-root identity and container controls. |
| Deployment/dumbassets | warn / 50% / network, images | baseline | Apply RuntimeDefault only. Defer non-root, no privilege escalation and drop ALL until image startup and writable-directory ownership are verified. |
| Deployment/changedetection | warn / 50% / network, images | baseline | Apply RuntimeDefault, preserving gVisor. Defer non-root, no privilege escalation and drop ALL until image startup and writable-directory ownership are verified. |

No profile reported a drift item. This is not proof of clean execution: executable inventory, prior export or security-context baseline is missing for the relevant drift checks. Investigate any later `unshippedExecutable` or `securityContextRegression` before rollout. Image vulnerability/signature data is not configured, so image safety remains unknown.

Observed syscall sets are available, but their timestamps can predate this collection and a missing SeccompProfile CR is not evidence that RuntimeDefault is absent. These changes select the container runtime default where defensible; they do not install an observation-derived syscall allowlist. Existing writable filesystems are retained. Capability recommendations are defaults, not continuous observations of capability use.

Validation passed: the required full repository validator (Flux/Helm rendering, shellcheck, secret guardrails and 25 regression tests), namespace chart renders, and before/after structural checks. Existing identities, added capabilities, privileged/host settings, application configuration and selectors are unchanged. Operator-created pods still need runtime verification: Helm can validate the CR, not the final reconciliation or application startup. Approval should include checking startup, storage writes, monitoring and scheduled jobs for each changed workload.

## Other kguardian features (2026-10-28)

These are sanitized read-only results, not live hardening or an audit pass.
The profile table above quotes posture coverage and unknown dimensions; unknown
never means safe. Raw pod addresses, credentials and captures are not published.

| Feature used | Evidence and decision |
| --- | --- |
| Workload inventory, profiles and revision diffs | 19 controller profiles and diffs reviewed; revision details below. Observed churn is not necessarily configuration drift. |
| Namespace traffic | 0 records, 0 pods, 0 drops at this snapshot. Coverage is incomplete; a rolling count is not a before/after enforcement delta. |
| Image inventory | 19 running digests in the returned inventory; inventory truncated: false. Inventory is not a vulnerability verdict. |
| Vulnerabilities and exposure | Namespace HIGH/CRITICAL query returned no findings; running-image reports are absent. No evidence-backed CVE id exists for `explain_cve_exposure`; do not invent one or claim zero vulnerabilities. |
| SBOM and signer queries | Queried all 19 returned running digests: no SBOM reports, and signers unknown/not_checked. This is neither unsigned nor verified/trusted. No trusted issuer/subject is inferred. |
| Image-trust explanation | Evaluation unavailable: signature discovery is off. No ImageTrustPolicy is added without verified signer evidence and owner-selected trust roots. |
| Syscalls and profile generation | Representative `nextcloud` pod returned 147 observed x86_64 syscalls. Generation failed with the architecture error below; no custom profile is bound. |
| Seccomp denials | Profile counters have zero observed denials. No SeccompProfile CR exists; this is not proof of complete enforcement coverage or safe custom allowlists. |
| Compute findings | 22 victims evaluated; 0 findings; truncated=false, history_disabled=false. This is a bounded observation window. |
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
| CronJob / nextcloud-cron | 1591 → 1592 | images | No peer changes |
| CronJob / satisfactory-restart | 17 → 18 | images, podSecurity | No peer changes |
| Deployment / changedetection | 47 → 48 | network | 1 removed: egress TCP/443 |
| Deployment / dumbassets | 3 → 4 | network | 2 removed: egress TCP/443, egress UDP/53 |
| Deployment / ghostfolio | 77 → 78 | network | 1 removed: egress TCP/443 |
| Deployment / homepage | 17 → 18 | network | 1 removed: egress UDP/53 |
| Deployment / it-tools | 2 → 3 | syscalls | No peer changes |
| Deployment / karakeep-chrome | 11 → 12 | network | 2 removed: egress TCP/443, egress UDP/443 |
| Deployment / karakeep-meilisearch | 16 → 17 | images | No peer changes |
| Deployment / karakeep | 24 → 25 | network | 2 added: egress TCP/7700, ingress TCP/3000 |
| Deployment / moonraker-obico | 150 → 151 | network | 1 added: ingress TCP/41375 |
| Deployment / nextcloud | 67 → 68 | images | No peer changes |
| Deployment / notify-push | 8 → 9 | network | 1 removed: egress TCP/80 |
| Deployment / obico-ml-api | 9 → 10 | images | No peer changes |
| Deployment / obico-server | 26 → 27 | network | 1 removed: egress TCP/443 |
| Deployment / picoshare | 1 → 2 | syscalls | No peer changes |
| Deployment / satisfactory | 607 → 608 | network | 1 added: ingress TCP/7777; 2 removed: ingress TCP/8888 |
| Deployment / searxng | 205 → 206 | images | No peer changes |
| Deployment / spoolman | 4 → 5 | network | 1 removed: egress UDP/53 |

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

[Policy](../../kubernetes/apps/default/networkpolicy.yaml) isolates web apps,
Karakeep's browser/index, Obico's ML API, Homepage widgets and scheduled work.
Narrow app policies remain additive. Satisfactory world ingress excludes FRM
8080; its existing game ports and broad Steam/game/mod egress remain explicit.
Nextcloud-to-notify-push 7867 is permitted in both directions. The five-minute
Nextcloud CronJob, daily Satisfactory restart and nightly Kopiur movers are covered
by stable selectors; movers retain API access for the filesystem repository.

Audit cold starts, browser/model fetches, optional integrations, callbacks,
notifications and backup/restore cycles. A newer Nextcloud CronJob had no data.
The pinned chart renders SMTP port **465** despite configured STARTTLS; confirm
mail delivery. Ghostfolio alone retains its documented direct-instance 5432
`DIRECT_URL` migration path for session advisory locks; steady-state traffic uses
`pgbouncer-rw`. Investigate session-pooler compatibility and the known same-node
primary VIP issue before changing that URL. Secret-backed endpoints remain unverified.
