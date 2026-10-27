# Default namespace policy draft

Phase 2 local proposal, 2026-10-28. Branch: `harden/kguardian-default-observability`;
worktree: `.claude/worktrees/kguardian-default-observability`; base: `fdd3cd954`.
No audit policy or enforcement has been deployed. This branch changes real CNP
manifests, so it must not be merged or reconciled before the separate audit and
owner approval gates.

The [namespace policy](../kubernetes/apps/default/networkpolicy.yaml) replaces
the four broad namespace ingress grants with app/port rules. Its first rule
denies unmatched ingress and egress, retaining host probes and unported kube-dns
reply ingress. Existing app ingress policies remain additive, including the
Satisfactory game ports. The existing deny-apiserver component is unchanged;
homepage, the restart Job and Kopiur keep their existing exceptions.

## Evidence and intended paths

Fresh namespace traffic, live workload/Service/route/policy inventory and generated
Cilium policies were checked for all 20 pod names: 19 generated successfully and
one newer Nextcloud CronJob had no captured traffic. A generated policy with no
egress rules also does not establish that an application needs no egress. Capture
duration is not provided by these responses; seven complete days are unproven.
The namespace traffic endpoint returned only one record/one pod and zero drops,
far less coverage than policy generation. That zero is not proof of healthy
traffic or an absence of drops; reconcile the coverage gap before audit.
Raw observations remain outside this public repository.

| Workload | Additional ingress and egress beyond host/DNS |
|---|---|
| Nextcloud app and five-minute CronJob | Public gateway to app pod 80; notify-push and nextcloud-exporter to app 80. Poolers 5432, Redis 6379, notify-push 7867, external gateway 10443 and internet 80/443/465. Includes root post-start apt downloads, app updates, callbacks and the chart-rendered SMTP port. |
| notify-push | Public gateway 7867; Nextcloud pod 80, poolers 5432 and Redis 6379. The Nextcloud Service exposes 8080 but targets pod 80. |
| Picoshare, Dumbassets, IT-Tools | Their configured gateway and HTTP port; no additional outbound grant justified by the current config. This is a candidate restriction, not a complete traffic inventory. |
| Ghostfolio | Internal gateway and AI ToolHive backend 3333; poolers 5432, Redis 6379, market HTTPS. A Ghostfolio-only direct Postgres instance 5432 exception preserves the documented `DIRECT_URL` migration path pending investigation. |
| Spoolman | Internal gateway 8000; poolers 5432 and observed metadata HTTPS. |
| Changedetection | Existing internal gateway 5000 rule; HTTP(S) checks and notification webhooks. |
| Karakeep app/browser/index | Internal gateway and ToolHive backend 3000 to the app; app to chrome 9222, Meilisearch 7700 and AI agentgateway 4000. Only app and chrome receive HTTP(S) internet egress. Existing sidecar ingress restricts both ports to the app controller. |
| Searxng | Existing internal gateway and ToolHive backend 8080; HTTP(S) and QUIC/UDP 443 search traffic. |
| Obico server and ML API | Internal gateway and host-network client path 3334 to server; server to ML API 3333 and Redis 6379. ML API receives no internet exception. |
| moonraker-obico | Existing hostNetwork exception remains; host/loopback/Janus flows require a separate host-policy design. |
| Satisfactory | Internal/public FRM routes and exporter 8080; existing world game ingress. World egress is intentionally broad for Steam/game/mod traffic and requires audit refinement. |
| Homepage | Internal gateway 3000; API discovery, exact configured media widgets, Grafana/Gatus/Kromgo, gateway TLS and public HTTPS site checks. |
| Satisfactory daily restart | Existing API opt-out label selects API 443/6443 egress; schedule remains 06:10 UTC. |
| Kopiur movers | Existing managed-by label selects API 443/6443 egress. Current repository is a node-mounted NFS filesystem; the mount is not an application pod NFS connection. |

The retained Jobs include scheduled movers for Changedetection, Dumbassets,
Karakeep, Nextcloud and Satisfactory, plus the Nextcloud cron and Satisfactory
restart Jobs. Backup configurations and the ClusterRepository were checked;
retained Jobs alone do not prove all nightly paths were captured.

## Rollout blockers

- Obtain at least seven days of capture including each CronJob, nightly backup,
  startup, upgrade, cold browser fetch, notification and mail path. Unknown drift
  dimensions from Phase 1 remain unknown; these manifests do not clear them.
- Investigate Ghostfolio `DIRECT_URL` separately. The chart comment requires
  session-level advisory locks; observed steady-state SQL uses `pgbouncer-rw`.
  No secret was read or decrypted to discover the startup URL. Confirm whether
  `pgbouncer-session` supports its migration client before changing that URL.
  The direct-instance exception does not solve the known same-node primary VIP
  blackhole and is not permission to migrate database configuration.
- The pinned Nextcloud chart renders `SMTP_PORT=465`, despite the configured
  STARTTLS setting; the draft preserves that actual port. Confirm mail delivery
  and the secret-backed database/read-replica endpoints during audit without
  committing secret values or making an unrelated mail configuration change.
- Exercise the zero-egress samples for Karakeep, Searxng, Picoshare, Dumbassets,
  IT-Tools, Satisfactory and Obico ML API. Confirm model acquisition, optional
  integrations and external notifications before removing or adding grants.
- Observe a complete Kopiur cycle and restart Job. A future remote repository
  transport needs an explicit reviewed mover rule; API exemptions are not an
  all-egress bypass.
- Review both ends of AI, media and observability integrations together. New
  Homepage annotations may introduce new widget peers.
- Before Phase 3, recheck the unmerged write-rate branch, capture exclusions and
  evaluator readiness. Keep `llmBridge` enabled. Express the proposal as audit
  CRs, review every legitimate `WouldDeny` for at least seven days, and obtain
  owner approval before replacing audit with CNP enforcement.

## Validation

The required `mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh`
passes full Flux/Helm rendering, shellcheck, secret guardrails and 24 guardrail
regression tests. The pinned Nextcloud chart was separately rendered to verify
SMTP and target-port assumptions. Native Cilium structural-schema checks use
the live CRD; local label/port assertions cover additive rules, AI peer pairs,
API denies, internet exceptions and host-network exclusions. These checks do
not simulate Cilium identities, NAT, host firewall behavior or seven days of traffic.
