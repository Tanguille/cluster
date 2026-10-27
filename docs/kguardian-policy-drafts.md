# Network policy draft and audit prerequisites

This proposal adds per-workload ingress and egress allowlists to eight namespaces.
**These are enforcing CNP manifests, not audit resources. Do not merge or reconcile
until the separate audit and owner approval gates are complete.** No live policy,
securityContext, kguardian setting or application credential was changed.

## Evidence

The 2026-10-28 read-only inventory had 22 CNPs, no CCNPs and 14 native policies.
Namespace traffic was queried before generating policies for all 137 present
pods, including retained Jobs: 122 policies returned and 15 requests had no data.
Generated histories and namespace traffic have inconsistent coverage:

| Namespace | Attempted / generated / no data | Traffic records / pods / drops |
|---|---|---|
| default | 20 / 19 / 1 | 1 / 1 / 0 |
| observability | 44 / 32 / 12 | 0 / 0 / 0 |
| media | 20 / 20 / 0 | 4957 / 2 / 176 |
| web3 | 4 / 4 / 0 | 5 / 1 / 1 |
| ai | 36 / 34 / 2 | 0 / 0 / 0 |
| flux-system | 6 / 6 / 0 | 0 / 0 / 0 |
| renovate | 5 / 5 / 0 | 24 / 1 / 0 |
| kritika | 2 / 2 / 0 | 9 / 4 / 0 |

Zeros and empty egress samples are **unknown**, not evidence of safety. The drops
above predate this proposal. Busy/P2P profiles are truncated; some capture spans
are shorter than seven days. Missing executable inventories and security-context
baselines leave drift unbounded. Investigate any new `unshippedExecutable` or
`securityContextRegression` finding first. No PSS compliance is claimed.

Stable labels and Service target ports replace captured pod hashes/addresses.
CronJobs, retained Jobs, backup configuration and scaled-zero workload templates
were checked separately. Raw captures, addresses and credentials are not committed.

## Shared policy behavior

- Default-deny both directions, with app-specific peer/port grants, host probes,
  kube-dns TCP/UDP 53 egress and unported DNS-reply ingress for reverse NAT.
- Preserve existing deny-apiserver overlays and their explicit controller/mover
  exceptions. A controller exemption does not grant every pod API access.
- Remove overlapping broad grants: namespace ingress, ToolHive backend access,
  memini/Konflate/Kritika chart policies, and speedtest's namespace-wide scrape
  selector. Retain narrow app policies and Kritika's native runner fallback.
- Internet access is role-specific. `world` HTTP/HTTPS grants are coarse protocol
  allowances, not authenticated host lists or an exfiltration guarantee. P2P
  applications need broader external ports; future changes need their own evidence.
- Match post-DNAT pod ports, including Envoy TLS 10443. A permitted edge needs both
  source egress and destination ingress. Host-network workloads need separate
  host-policy analysis; node image pulls and node-mounted NFS are not pod egress.

## Namespace review notes

### Default

[Policy](../kubernetes/apps/default/networkpolicy.yaml) isolates web apps,
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

### Observability

[Policy](../kubernetes/apps/observability/networkpolicy.yaml) separates scrapers,
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

### Media

[Policy](../kubernetes/apps/media/networkpolicy.yaml) permits named Arr/download,
subtitle, invitation, playback, widget and exporter clients. Only Jellyfin 8096,
Seerr 5055 and Wizarr 5690 accept the public gateway. Existing Flaresolverr rules
remain narrow. SQL clients use poolers; Bitmagnet/prune require the session pooler.
Seerr/Jellystat gateway hairpins use 10443. Unpackerr uses current port **5656**,
including Hermes access; historical 16373 is stale.

Only qBittorrent and main Bitmagnet get unrestricted world P2P egress. FileFlows'
root-startup package retrieval and Armiger's package/model downloads remain;
security contexts are untouched. Recyclarr/prune schedules and Kopiur movers have
separate grants. Audit these rare paths, provider redirects, notifications and
backups, and investigate Jellyfin's unexplained LAN UDP/65001 before enforcement.
Historical qBitrr and migration Jobs do not justify grants to future workloads.

### Web3

[Policy](../kubernetes/apps/web3/networkpolicy.yaml) separates monerod, p2pool,
miners, dashboard and guard. Monerod/p2pool retain variable-port world P2P egress;
miners get stratum 3333 and dashboard API ingress 42000. Guard reaches vmauth 8427
and exposes metrics only. LoadBalancer listener grants still depend on existing
`${LAN_CIDR}` source ranges and preserved client identity; `world` is not LAN-only.

All three miners were scaled to zero. Audit scale-up/down, reconnects, peer churn,
dashboard fallbacks and monitoring failures. XMRig donation is not explicitly
disabled; its unobserved destinations need investigation before any internet grant
or owner-approved command change. No scaling or guard thresholds changed.

### AI

[Namespace](../kubernetes/apps/ai/networkpolicy.yaml),
[ToolHive](../kubernetes/apps/ai/toolhive/app/networkpolicy.yaml) and
[inference](../kubernetes/apps/ai/llmkube/models/networkpolicy.yaml) rules preserve
named model/MCP clients, matching proxy backends, API management roles, model init
downloads and scheduled/mover work. Agentgateway xDS 9978 and Unified's configured
Redis dependency are included despite absent samples. Memini's broad native
policy is disabled; embedding-cache reaches only its embedder and agent direct
inference access stays limited to the named reranker.

Resolve Hermes SSH/1925/4096 attribution, Headroom HTTPS despite offline mode,
and shared ToolHive operator/proxy HTTPS that is not the Kubernetes Service IP.
No guessed SSH/LAN/internet grants were added. Karakeep and the KV-guard Job had
no data. Audit every MCP transport, Home Assistant's selectorless
Service, node-based Talos access, browser/code tasks, cold models, Hermes schedules,
cache pruning, vMCP restart and nightly movers. Detached Paseo is not active and
needs its own policy/evidence before activation; it does not inherit Hermes rules.

### Flux system

[Policy](../kubernetes/apps/flux-system/networkpolicy.yaml) permits artifact/event
pod port 9090, external webhook 9292, internal operator UI 9080, vmagent metrics,
controller API/HTTPS and Konflate public rendering/image-verification HTTPS.
Konflate accepts the public gateway and AI Unified (`virtualmcpserver`, instance
`unified`) on 8080, but receives no API grant. Its additive chart policy is disabled.
There were no CronJobs/Jobs or configured movers in this namespace.

Audit source refreshes, chart installation/upgrades, remote bases, receiver hooks,
provider notifications, image verification and cold renders. Zero namespace
traffic records despite six generated policies is a coverage gap.

### Renovate

[Policy](../kubernetes/apps/renovate/networkpolicy.yaml) gives the operator internal
UI 8081, public webhook 8082, vmagent metrics 8080, API, Dragonfly 6379 and HTTPS.
Stable discovery/executor Job labels allow cache and public HTTPS, without API
access. Only executors retain observed TCP/80; verify those destinations. The
chart's admission policy is unrelated and remains enabled.

No Kubernetes CronJob exists: the RenovateJob runs every 30 minutes and the
snapshot contained four Jobs. Audit every repository/datasource, discovery,
tool downloads, token refresh, cache and webhook synchronization. Capture must
include the namespace; the unmerged write-rate branch proposed excluding it.

### Kritika

[Policy](../kubernetes/apps/kritika/networkpolicy.yaml) replaces chart-generated
server allow-all ingress and runner `ingress: [{}]`. Server paths remain Envoy
8080, vmagent 8081, runner gateway 8082, API, model gateway 4000, DB and HTTPS.
Runners have only service-name DNS, DB and gateway egress; no internet/API grant.
The native DB/gateway-only fallback has no DNS and fails closed if Cilium rejects
the L7 rule. gVisor and chart admission controls remain enabled. The public
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

## Validation and next gate

Each original workstream passed `validate-pr.sh`, the deployed Cilium schema,
selector/peer checks and independent review. Pinned Nextcloud, speedtest, memini
and Kritika charts were rendered to check actual ports and policy unions.
The combined branch passed the required repository validator after simplification:
Flux/Helm rendering, shellcheck, secret checks and 25 regression tests. All five
simplified policy files resolve to exactly the same YAML data as the original
drafts, preserving selectors, ports, protocols and rule order while removing
634 lines. Repeated evidence and workflow notes are consolidated here.
Local static checks do not prove Cilium identities, NAT, application health or audit success.

Before enforcement:

1. Refresh live inventory, capture exclusions and evaluator support. The write-rate
   branch was unmerged at inspection; evaluator/compute were on and capture excluded
   only kguardian and kube-system. Keep `llmBridge` enabled. Cilium L7/transparent
   DNS proxy were enabled, but verify again before using the runner DNS restriction.
2. Prepare a separate AuditNetworkPolicy deployment for owner approval. Confirm the
   evaluator faithfully models entities, service accounts, selectorless Services,
   CIDRs, ICMP, DNS/L7 restrictions and additive policy unions. Unsupported dimensions
   cannot yield a clean verdict; do not deploy these CNPs as an audit experiment.
3. Observe at least seven complete days, including the rare paths above. Resolve
   every legitimate `WouldDeny` with its evidence and investigate drift first.
4. Only after explicit owner approval, enforce and watch drop deltas and app health.
   A passing local check, draft PR or historical review is not that approval.
