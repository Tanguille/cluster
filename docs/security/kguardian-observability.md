# observability: kguardian hardening draft

Evidence collected on 2026-10-28. This is a reviewable GitOps proposal: no cluster configuration was applied or reconciled. Network restrictions and workload contexts require owner approval before rollout. The network draft still requires at least seven days of representative audit evidence, including scheduled jobs and backups.

The table records the **current observed posture**, not a prediction after these changes. Coverage is kguardian overall coverage; unknown dimensions are listed explicitly. PSS values are **upper bounds only**, with 9 of 18 checks evaluated where data exists. A `restricted` upper bound does not establish restricted compliance. `Apply` means proposed in this branch, `already` means retained, and exemptions/deferred items remain unresolved restrictions.

| Workload | Posture / coverage / unknown dimensions | PSS upper bound | Recommendation decision and evidence |
| --- | --- | --- | --- |
| DaemonSet/victoria-logs-collector | warn / 50% / network, images | baseline | Apply RuntimeDefault and no privilege escalation. Defer non-root/drop ALL: the collector reads host-owned logs; retain current identity and capabilities until permissions are tested. |
| DaemonSet/smartctl-exporter-0 | risk / 50% / network, images | privileged | Exemption: privileged root access is required by the current host-device configuration. Defer suggested non-root, capability removal and seccomp changes pending device-access validation. |
| DaemonSet/power-monitor | risk / 50% / network, images | privileged | Exemption: retain SYS_PTRACE and existing no-privilege-escalation/drop-ALL/read-only controls for process telemetry. Defer non-root and RuntimeDefault until operator support and hardware syscall compatibility are demonstrated. |
| DaemonSet/node-exporter | risk / 50% / network, images | privileged | Apply RuntimeDefault, no privilege escalation and drop ALL. Retain pinned chart uid/gid/fsGroup 65534 and hostNetwork for node telemetry; validate collector coverage during an approved rollout. |
| DaemonSet/igpu-gtt-exporter | risk / 50% / network, images | privileged | Exemption: retain root, SYS_PTRACE and hostPID for host process fdinfo access. RuntimeDefault, no privilege escalation, drop ALL and read-only filesystem already configured. |
| DaemonSet/drm-exporter | risk / 50% / network, images | privileged | Exemption: preserve root, PERFMON/SYS_RAWIO and Unconfined seccomp for documented GPU access. Do not replace these without hardware/syscall testing. |
| StatefulSet/vmalertmanager-victoria-metrics | warn / 50% / network, images | baseline | Apply RuntimeDefault, non-root, no privilege escalation and drop ALL through native VMAlertmanager securityContext. Explicitly preserve the chart fallback uid/gid/fsGroup 1000. |
| StatefulSet/victoria-logs | warn / 50% / network, images | baseline | Apply RuntimeDefault; preserve chart uid 1000/fsGroup 2000 and existing no-privilege-escalation/drop-ALL/read-only controls. |
| Deployment/vmsingle-victoria-metrics | warn / 50% / network, images | baseline | Apply RuntimeDefault and no privilege escalation through native operator securityContext. Defer non-root/drop ALL until image user, storage ownership and init/reloader behavior are verified; preserve existing identity and writable paths. |
| Deployment/vmauth-victoria-metrics | warn / 50% / network, images | baseline | Apply RuntimeDefault and no privilege escalation through native operator securityContext. Defer non-root/drop ALL until image user, storage ownership and init/reloader behavior are verified; preserve existing identity and writable paths. |
| Deployment/vmagent-victoria-metrics | warn / 50% / network, images | baseline | Apply RuntimeDefault and no privilege escalation through native operator securityContext. Defer non-root/drop ALL until image user, storage ownership and init/reloader behavior are verified; preserve existing identity and writable paths. |
| Deployment/vmalert-victoria-metrics | warn / 50% / network, images | baseline | Apply RuntimeDefault and no privilege escalation through native operator securityContext. Defer non-root/drop ALL until image user, storage ownership and init/reloader behavior are verified; preserve existing identity and writable paths. |
| Deployment/victoria-metrics-victoria-metrics-operator | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain pinned operator chart identity and container controls. |
| Deployment/speedtest-exporter | warn / 50% / network, images | baseline | Apply RuntimeDefault and read-only root; retain uid/gid 1000 and the existing writable /cache PVC. |
| Deployment/sonarr-exporter | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain uid/gid 568 and existing container controls. |
| Deployment/siren | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain uid/gid 1000 and existing container controls. |
| Deployment/silence-operator | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/scraparr | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/satisfactory-frm-exporter | warn / 50% / network, images | baseline | Apply RuntimeDefault and verified uid/gid 65534 with runAsNonRoot; retain existing no-privilege-escalation/drop-ALL/read-only controls. |
| Deployment/qbittorrent-exporter | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain uid/gid 568 and existing container controls. |
| Deployment/prowlarr-exporter | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain uid/gid 568 and existing container controls. |
| Deployment/opnsense-exporter | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain uid/gid 568 and existing container controls. |
| Deployment/nut-exporter | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/nextcloud-exporter | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain uid/gid 568 and existing container controls. |
| Deployment/kube-state-metrics | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/kromgo | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/klipper-exporter | warn / 50% / network, images | baseline | Apply RuntimeDefault. Defer runAsNonRoot until the image user/startup contract is verified; retain existing no-privilege-escalation/drop-ALL/read-only controls. |
| Deployment/kepler-operator-controller | warn / 50% / network, images | baseline | Apply RuntimeDefault through the chart manager-container securityContext; retain non-root, no privilege escalation and drop ALL. This does not change PowerMonitor-generated pods. |
| Deployment/keda-operator-metrics-apiserver | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/keda-operator | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/keda-admission-webhooks | warn / 25% / network, podSecurity, images | restricted | Already configured: retain non-root identity, no privilege escalation, dropped capabilities and RuntimeDefault; no new context changes. |
| Deployment/grafana-operator | warn / 50% / network, images | baseline | Apply RuntimeDefault; retain pinned chart non-root identity and container controls. |
| Deployment/grafana-deployment | warn / 50% / network, images | baseline | Apply RuntimeDefault through the Grafana CR pod template, covering Grafana and plugin initialization; retain uid/gid/fsGroup 1000 and current writable paths. |
| Deployment/gatus | risk / 50% / network, images | privileged | Apply RuntimeDefault. Exemption: retain NET_RAW for configured ICMP health checks and existing non-root/container controls. |
| Deployment/blackbox-exporter | risk / 50% / network, images | privileged | Apply RuntimeDefault. Exemption: retain NET_RAW for configured ICMP probes and existing non-root/container controls. |

No profile reported a drift item. This is not proof of clean execution: executable inventory, prior export or security-context baseline is missing for the relevant drift checks. Investigate any later `unshippedExecutable` or `securityContextRegression` before rollout. kguardian vulnerability/signature ingestion is not configured; the separate Trivy follow-up below records available operator reports. Missing scan or signature coverage remains unknown.

Observed syscall sets are available, but their timestamps can predate this collection and a missing SeccompProfile CR is not evidence that RuntimeDefault is absent. These changes select the container runtime default where defensible; they do not install an observation-derived syscall allowlist. Existing writable filesystems are retained except speedtest-exporter, whose cache already has a writable PVC. Capability recommendations are defaults, not continuous observations of capability use.

Validation passed: the required full repository validator (Flux/Helm rendering, shellcheck, secret guardrails and 25 regression tests), namespace chart renders, and before/after structural checks. Existing identities, added capabilities, privileged/host settings, application configuration and selectors are unchanged. Operator-created pods still need runtime verification: Helm can validate the CR, not the final reconciliation or application startup. Approval should include checking startup, storage writes, monitoring and scheduled jobs for each changed workload.

VictoriaMetrics operator v0.75.2 accepts pod and container fields directly under `spec.securityContext`; the pinned [API type](https://github.com/VictoriaMetrics/operator/blob/v0.75.2/api/operator/v1beta1/vmextra_types.go) and [container builder](https://github.com/VictoriaMetrics/operator/blob/v0.75.2/internal/controller/operator/factory/build/security.go) were checked because its CRD preserves unknown nested fields. `useStrictSecurity` is deliberately not enabled: its additional identity/filesystem changes would require separate validation.

## Other kguardian features (2026-10-28)

These are sanitized read-only results, not live hardening or an audit pass.
The profile table above quotes posture coverage and unknown dimensions; unknown
never means safe. Raw pod addresses, credentials and captures are not published.

| Feature used | Evidence and decision |
| --- | --- |
| Workload inventory, profiles and revision diffs | 35 controller profiles and diffs reviewed; revision details below. Observed churn is not necessarily configuration drift. |
| Namespace traffic | 0 records, 0 pods, 0 drops at this snapshot. Coverage is incomplete; a rolling count is not a before/after enforcement delta. |
| Image inventory | 36 running digests in the returned inventory; inventory truncated: false. Inventory is not a vulnerability verdict. |
| Vulnerabilities and exposure | kguardian's HIGH/CRITICAL query returned no findings because its image reports are absent. Separate Trivy findings appear below; kguardian exposure evaluation still needs ingested reports. This is not zero vulnerabilities. |
| SBOM and signer queries | Queried all 36 returned running digests: no SBOM reports, and signers unknown/not_checked. This is neither unsigned nor verified/trusted. No trusted issuer/subject is inferred. |
| Image-trust explanation | Evaluation unavailable: signature discovery is off. No ImageTrustPolicy is added without verified signer evidence and owner-selected trust roots. |
| Syscalls and profile generation | Representative `gatus` pod returned 85 observed x86_64 syscalls. Generation failed with the architecture error below; no custom profile is bound. |
| Seccomp denials | Profile counters have zero observed denials. No SeccompProfile CR exists; this is not proof of complete enforcement coverage or safe custom allowlists. |
| Compute findings | 48 victims evaluated; 0 findings; truncated=false, history_disabled=false. This is a bounded observation window. |
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
| DaemonSet / drm-exporter | 12 → 13 | images | No peer changes |
| DaemonSet / igpu-gtt-exporter | 17 → 18 | images | No peer changes |
| DaemonSet / node-exporter | 111 → 112 | network | 2 removed: egress TCP/3300, ingress TCP/2380 |
| DaemonSet / power-monitor | 22 → 23 | network | 1 added: egress TCP/10250 |
| DaemonSet / smartctl-exporter-0 | 10 → 11 | images | No peer changes |
| DaemonSet / victoria-logs-collector | 6 → 7 | network | 1 added: egress TCP/9428 |
| Deployment / blackbox-exporter | 21 → 22 | network | 1 removed: ingress TCP/9115 |
| Deployment / gatus | 28 → 29 | images | No peer changes |
| Deployment / grafana-deployment | 24 → 25 | images | No peer changes |
| Deployment / grafana-operator | 21 → 22 | network | 1 removed: ingress TCP/9090 |
| Deployment / keda-admission-webhooks | 8 → 9 | network | 1 removed: ingress TCP/9443 |
| Deployment / keda-operator-metrics-apiserver | 11 → 12 | network | 1 removed: egress TCP/9666 |
| Deployment / keda-operator | 8 → 9 | network | 1 added: egress TCP/6443 |
| Deployment / kepler-operator-controller | 12 → 13 | images | No peer changes |
| Deployment / klipper-exporter | 7 → 8 | network | 1 removed: ingress TCP/9101 |
| Deployment / kromgo | 11 → 12 | images | No peer changes |
| Deployment / kube-state-metrics | 6 → 7 | network | 1 removed: egress TCP/6443 |
| Deployment / nextcloud-exporter | 6 → 7 | images | No peer changes |
| Deployment / nut-exporter | 5 → 6 | network | 1 removed: ingress TCP/9199 |
| Deployment / opnsense-exporter | 8 → 9 | images | No peer changes |
| Deployment / prowlarr-exporter | 9 → 10 | network | 1 removed: ingress TCP/9707 |
| Deployment / qbittorrent-exporter | 7 → 8 | images | No peer changes |
| Deployment / satisfactory-frm-exporter | 2 → 3 | network | 1 added: egress UDP/53 |
| Deployment / scraparr | 11 → 12 | network | 1 removed: egress TCP/8096 |
| Deployment / silence-operator | 11 → 12 | network | 1 removed: ingress TCP/8080 |
| Deployment / siren | 4 → 5 | network | 1 removed: egress UDP/53 |
| Deployment / sonarr-exporter | 4 → 5 | network | 1 removed: ingress TCP/9707 |
| Deployment / speedtest-exporter | 13 → 14 | network | 3 added: egress TCP/8080 |
| Deployment / victoria-metrics-victoria-metrics-operator | 9 → 10 | network | 1 added: egress TCP/6443 |
| Deployment / vmagent-victoria-metrics | 314 → 315 | network | 2 added: egress TCP/8080 |
| Deployment / vmalert-victoria-metrics | 18 → 19 | images | No peer changes |
| Deployment / vmauth-victoria-metrics | 27 → 28 | network | 1 added: egress TCP/6443 |
| Deployment / vmsingle-victoria-metrics | 30 → 31 | network | 3 removed: ingress TCP/8428 |
| StatefulSet / victoria-logs | 22 → 23 | network | 3 removed: ingress TCP/9428 |
| StatefulSet / vmalertmanager-victoria-metrics | 17 → 18 | images | No peer changes |

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

[Policy](../../kubernetes/apps/observability/networkpolicy.yaml) separates scrapers,
exporters, query/write services, alert delivery, operators and UIs. Gatus uses
its actual `gatus-sidecar` label; only its probe gateway is public. Kromgo uses
pod ports 8080/8081, not Service ports 80/8080. Hermes and Ceph-manager writes,
CrowdSec alerts, exporter application calls and admission callbacks are retained.

vmagent deliberately reaches pods across namespaces for dynamic monitors through
an explicit namespace-exists selector; node/API/LAN paths are separately scoped.
Its unexplained external TCP/9090 sample is not allowed. Twelve exporters/log
collectors had no data, including host-network samples. Audit scrape discovery,
webhook/aggregated-API source identities, Gatus ICMP/DNS/MCP checks, substituted
LAN targets, Grafana pooler/plugin downloads, log metadata and alert webhooks.
The speedtest native policy now admits only vmagent. Node-exporter remains outside
pod policy; all host/NAT identity assumptions need runtime verification.

## Trivy follow-up: 2026-10-28

The [image ledger](trivy-observability-images.csv) covers 27 image rows; 8 Critical and 572 High deduplicated package findings. The [finding ledger](trivy-observability-findings.csv) retains every Critical/High id, package, installed version and all reported fixed versions, deduplicated per digest/id/package/version/severity. Raw report counts remain alongside deduplicated counts. Reports were joined to active owner uids and checked against current regular/init container spec pins and status imageIDs; historical reports were also checked for exact current digest matches. Source report names, timestamps and ages are retained. These are observed findings, not a claim that every affected package is reachable.

Most pinned tags still identify the same publisher image, and many applications are already on their latest published release. A fixed package version does not imply a supported patched application image exists. No findings are suppressed, no packages are upgraded at startup, and no custom image forks are introduced. Fresh scanning remains unavailable through the discovered MCP tools: kguardian returns no vulnerability data even for a VM operator digest with an existing Trivy report. That is an ingestion/coverage gap, not zero vulnerabilities.

Current-owner configuration findings are accounted for below. Repeated checks for multiple containers are grouped; the table describes the remaining requirement rather than treating a retained exception as resolved. Several configuration reports predate the latest image scan; for example node-exporter has an older report despite its current owner uid.

| Workload | Failed High checks | Draft action or blocker |
| --- | --- | --- |
| DaemonSet/drm-exporter | AVD-KSV-0121 | Exemption: /sys access is required for the current GPU collector; retain its documented hardware settings. |
| DaemonSet/igpu-gtt-exporter | AVD-KSV-0010 | Exemption: hostPID is required for process fdinfo telemetry. Image index equivalence is separately recorded below. |
| DaemonSet/node-exporter | AVD-KSV-0009, AVD-KSV-0010, AVD-KSV-0121 | Exemption: hostPID, hostNetwork and host /proc, /sys and filesystem mounts supply node metrics. Container context hardening does not remove these required host accesses. |
| DaemonSet/power-monitor | AVD-KSV-0010, AVD-KSV-0118, AVD-KSV-0121 | Exemption/deferred: host process/sysfs telemetry needs hostPID and mounts; root/default-context removal requires operator and hardware validation. |
| DaemonSet/smartctl-exporter-0 | AVD-KSV-0014, AVD-KSV-0017, AVD-KSV-0118, AVD-KSV-0121 | Exemption/deferred: privileged root and /dev access support disk health reads; read-only-root compatibility still needs validation. |
| DaemonSet/victoria-logs-collector | AVD-KSV-0014, AVD-KSV-0118 | Deferred: host-owned log access, checkpoints and temporary paths require testing. Proposed no-privilege-escalation does not establish non-root/read-only compatibility. |
| Deployment/blackbox-exporter | AVD-KSV-0118, AVD-KSV-0119 | Exemption: NET_RAW supports ICMP. Review the scanner default-context result against the rendered non-root container before altering its pod identity. |
| Deployment/gatus | AVD-KSV-0119 | Exemption: NET_RAW supports configured ICMP checks. |
| Deployment/grafana-operator | AVD-KSV-0118 | Pending scanner verification: retain rendered non-root container controls; do not infer that a pod-level default-context finding proves the container runs as root. |
| Deployment/kepler-operator-controller | AVD-KSV-0014 | Deferred: controller temporary/certificate writes need validation before making its filesystem read-only. |
| Deployment/klipper-exporter | AVD-KSV-0118 | Deferred: pinned image starts ./main from /root; forcing a non-root uid without validating directory traversal would risk startup. |
| Deployment/satisfactory-frm-exporter | AVD-KSV-0118 | Proposed fix: uid/gid 65534 and non-root; retain existing read-only root, no privilege escalation and drop ALL. Image contains only the executable and mode-0755 parent directories. |
| Deployment/speedtest-exporter | AVD-KSV-0014 | Proposed fix: read-only root; pinned chart already mounts its writable cache PVC at /cache and supplies configuration as a mounted file. |
| Deployment/vmagent-victoria-metrics | AVD-KSV-0014, AVD-KSV-0118 | Deferred: verify init/reloader, buffering and filesystem ownership before adding non-root/read-only controls; current RuntimeDefault/no-privilege-escalation is partial. |
| Deployment/vmalert-victoria-metrics | AVD-KSV-0014, AVD-KSV-0118 | Deferred: verify init/reloader and writable paths before adding non-root/read-only controls; current RuntimeDefault/no-privilege-escalation is partial. |
| Deployment/vmauth-victoria-metrics | AVD-KSV-0014, AVD-KSV-0118 | Deferred: verify init/reloader and writable paths before adding non-root/read-only controls; current RuntimeDefault/no-privilege-escalation is partial. |
| Deployment/vmsingle-victoria-metrics | AVD-KSV-0014, AVD-KSV-0118 | Deferred: verify TSDB ownership and runtime paths before adding non-root/read-only controls; preserve existing data identity. |
| StatefulSet/vmalertmanager-victoria-metrics | AVD-KSV-0014 | Deferred: app and init/reloader read-only-root compatibility remains untested; retain current uid/gid/fsGroup 1000. |

The operator and config-reloader are proposed at upstream v0.75.2 using the pinned chart’s native `image` and `configReloader.image` values. Exact candidate digests are in the image ledger. Read-only extraction of each candidate `/app` binary and `go version -m` proved **Go 1.27.3 and `golang.org/x/net` 0.61.0**; neither binary was executed. Those versions meet the fixed-version requirements of all five current High findings on each old image. This is package-version evidence, not a fresh full-image vulnerability scan. The [release](https://github.com/VictoriaMetrics/operator/releases/tag/v0.75.2) and [source dependencies](https://github.com/VictoriaMetrics/operator/blob/v0.75.2/go.mod) were checked; the source minimum Go version alone was not used to infer the compiled version.

FRM evidence: the pinned image `docker.io/featheredtoast/ficsit-remote-monitoring-companion:latest@sha256:6c003115f5b748687cdff1dea963d956a005fee6239265e357ea8b14c7bfb18b` contains only `/usr/local/bin/companion` and its parent directories, all mode 0755. It already runs with a read-only filesystem, drops all capabilities, and listens on unprivileged port 9000. Setting uid/gid 65534 does not require changes to mounts or writable paths. Its existing image vulnerabilities remain unresolved.

Speedtest evidence: the pinned v1.8.1 chart defaults `config.persistCache: true` and mounts its cache PVC at `/cache`; the [image Dockerfile](https://github.com/Heathcliff26/speedtest-exporter/blob/v1.8.1/Dockerfile) declares the same cache path. The draft changes only `securityContext.readOnlyRootFilesystem`, preserving the PVC, uid/gid and cache behavior.

Image index verification: igpu-gtt-exporter spec pin `sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913` and runtime imageID `sha256:80cd82871b55326f05d4c3a3b714b371b0e9833891de4be4eb4b99d3092e0029` are different OCI indexes selecting the same linux/amd64 manifest, `sha256:3053ea19f29305df68292b41291d621e0cbc9c1c826822c631713891ea0b64e9`, with identical config and layers. This is equivalent runtime content, not workload drift. The runtime-index report from web3/guard was checked as a historical-owner fallback: its 48 High findings are a subset of the 49 reported against the spec pin. The ledger retains that source and the equivalent digest without double-counting them. Likewise, Scraparr’s current tag index contains its exact pinned amd64 digest; changing only that pin would not fix its vulnerabilities.

Rollout and rescan are pending owner approval. Retained root, startup, writable-filesystem, host/device and ICMP exceptions remain visible findings; they are not suppressed or reported as fixed.
