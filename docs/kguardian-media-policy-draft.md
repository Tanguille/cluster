# Media network policy draft

Local Phase 2 draft on `harden/kguardian-media-web3`, worktree
`.claude/worktrees/kguardian-media-web3`, based on `fdd3cd954`. No policy has been
applied, reconciled, pushed, or submitted as a PR. The CNP file is an enforcement
candidate, **not an audit resource**; merging it would enforce it through Flux.

## Evidence and scope

On 2026-10-28, read-only workload, Pod, Service, CronJob, Job and network policy
inventory was compared with kguardian traffic and generated Cilium policies for
all 20 current media pods, including Bitmagnet's prune Job and a manual Recyclarr
Job. Generated policy histories contain stale ReplicaSets and the removed qBitrr;
those identities were not preserved. Stable labels replace pod hashes and peers'
addresses. Raw observations remain outside this public repository.

The traffic summary returned only two pods, 4,957 records and 176 existing drops;
it is not proof of namespace coverage or an enforcement result. High-churn P2P
histories are truncated, and some workload observation windows are shorter than
seven days. Missing observations are unknown, not evidence that a path is safe
to deny. Drift remains unbounded where executable inventory or security-context
baselines are absent; this draft does not resolve those Phase 1 findings.

The namespace policy now defaults to denying ingress and egress, with host probes,
DNS TCP/UDP 53 and unported kube-dns reply ingress. Application rules replace
the old whole-namespace caller grants. The narrow Flaresolverr policy still unions
with the namespace policy. Live inventory showed no other native NetworkPolicies
in media; the deny-apiserver component remains unchanged.

| Dependency | Evidence and draft treatment |
| --- | --- |
| External routes | Only Jellyfin 8096, Seerr 5055 and Wizarr 5690 accept the external Envoy gateway. Internal UIs use the internal gateway and their own ports. |
| Widgets and metrics | Homepage, Scraparr, app exporters and vmagent have distinct application selectors and service ports. |
| Arr/download integrations | Generated flows identify the directed Radarr/Sonarr/Prowlarr/qBittorrent/Qui paths, cleaners, subtitles, Jellyfin integrations and invitation flow. No blanket media-to-media grant remains. |
| Pooling | Bazarr, Jellystat, Prowlarr, Radarr and Sonarr generated flows identify `pgbouncer-rw:5432`. Bitmagnet and its prune configuration use `pgbouncer-session:5432`. Direct primary VIP access is not introduced. |
| Gateway hairpin | Jellystat and Seerr currently reach the external Envoy gateway on 10443 for configured public hostnames. This path is retained explicitly; changing the application URL is separate work. |
| Internet | Only qBittorrent and the main Bitmagnet controller receive unrestricted world egress for arbitrary P2P peers. Web providers use TCP 80/443 or 443 according to their roles; Flaresolverr also has observed QUIC UDP 443. |
| Bootstrap | FileFlows retains HTTP/HTTPS package retrieval during its documented root startup. Armiger has HTTPS package/model retrieval, scoped separately from Bitmagnet P2P. No securityContext is changed. |
| Scheduled work | Recyclarr can retrieve Git-hosted definitions and call both Arr APIs. Bitmagnet prune can call its GraphQL service and session pooler. These selectors cover CronJob and manual Job pods. |
| Backups | Kopiur mover labels retain API 443/6443 and DNS access. The configured filesystem repository uses node-mounted NFS; no guessed S3 or pod-to-NAS grant is added. Existing API-deny exclusions remain. |
| Unpackerr | Current v1 Service, generated traffic and vmagent use 5656; historical 16373 is stale. Hermes is the explicit ai caller. |

## Audit readiness blockers

- Obtain at least seven days of successful audit observation across every app,
  daily Recyclarr/prune and nightly Kopiur backup/restore paths. Completed historical
  `volsync-copy-data` Jobs are not a grant for arbitrary future migration Jobs.
- Verify evaluator availability against the installed release. The separate
  write-rate branch must be checked again before audit; retain `llmBridge`.
  Audit CR creation and evaluator changes require owner approval.
- Investigate Jellyfin's earlier unexplained LAN UDP 65001 observation before
  enforcement; no broad LAN exception was invented. Confirm the gateway hairpin
  path's destination identity across rollouts, including the historical unlabeled
  10443 record, and all configured notification/webhook paths.
- Exercise cold starts, provider redirects and sparse paths: FileFlows packages,
  Armiger bootstrap, subtitle providers, public invitations, playback, downloads,
  Recyclarr definitions and backups. HTTP/HTTPS world permissions still allow
  broad external access; they are not a domain allowlist or exfiltration guarantee.
- Recheck all policy unions and service ports after concurrent namespace drafts
  are combined. Both source egress and destination ingress must permit the same
  service; an allowed CNP edge alone does not prove application health.
- Resolve every legitimate `WouldDeny` with named evidence before proposing
  enforcement. New drift findings take priority. Neither PSS compliance nor
  runtime safety follows from these network rules.

## Validation

`mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh` passed Flux/Helm
rendering, shellcheck, secret guardrails and 24 regression tests. The deployed
Cilium CRD schema accepts the namespace and Flaresolverr policies. Selector checks
cover all 20 media workload/CronJob templates. Across media and web3, 62 generated
current internal egress edges, 54 current ingress edges and 24 positive/negative
cases pass the static policy-union check. Historical qBitrr sources are intentionally
excluded. `git diff --check` passes. These are static checks, not audit verdicts
or production proof.

Next phase requires owner confirmation. Suggested continuation: “Prepare the
media AuditNetworkPolicy resources and evaluator prerequisite for review; do not
deploy them.”
