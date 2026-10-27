# Kritika namespace network policy draft

Phase 2, 2026-10-28. Branch `harden/kguardian-controllers`, worktree
`.claude/worktrees/kguardian-controllers`, base `fdd3cd954`.
Local proposal only: these CNP/native policies would enforce if deployed.
No push, PR, apply, reconciliation or audit deployment has occurred.
Verify branch/worktree before continuing.

## Evidence and policy union

Generated policies were collected for the current server and runner pod.
Namespace traffic returned nine records across four historical/current pods
and zero drops; this is partial coverage. The earlier server profile was warn,
coverage 25%, unknown network/podSecurity/images. Its October 13 onward network
table was truncated, so the retained span does not establish complete capture.

The chart's CNP allowed all peers to server ports 8080/8081 and allowed every
ingress peer to runners through `ingress: [{}]`. Its native runner policy could
not narrow that additive Cilium grant. Disable chart network policies and
replace them with the namespace CNP plus a native runner fallback.

- Server ingress: Envoy internal/external on 8080, vmagent on 8081, runners on
  gateway 8082. HTTPRoute still limits the public surface to `/hooks/`.
- Server egress: kube-dns, Kubernetes API 443/6443, agentgateway in AI on pod
  port 4000, PostgreSQL instances on 5432, and public HTTPS 443.
- Runners: service-only DNS, PostgreSQL 5432 and server gateway 8082. No API or
  internet grant and no application ingress. Host probes and unported kube-dns
  reply ingress remain baseline exceptions.
- Keep the native fallback's DB/gateway-only egress with no DNS. If the Cilium
  L7 policy is refused, runners fail closed rather than losing confinement.

Cilium's L7 proxy and transparent DNS proxy were enabled at inspection. No
unrestricted runner DNS rule unions with the service-name restriction. Runner
Jobs are created dynamically; the snapshot had one Job and no CronJobs or
configured Kopiur movers here. Existing API-deny overlays elsewhere are intact.

## Direct PostgreSQL exception rechecked

The owner requested a fresh compatibility check. Pinned Kritika chart/source
`0.0.109` (source commit `a801d488a237563874c00a6a9d3c581fe4b1477e`)
sets `tcp_keepalives_idle`, `tcp_keepalives_interval`, `tcp_keepalives_count`
and `statement_timeout` as startup parameters in
[poolConfig](https://github.com/home-operations/kritika/blob/0.0.109/internal/store/store.go).
It also uses a dedicated
[LISTEN connection](https://github.com/home-operations/kritika/blob/0.0.109/internal/store/notify.go)
and [session advisory locks](https://github.com/home-operations/kritika/blob/0.0.109/internal/store/leader.go).

Read-only PostgreSQL startup handshakes through localhost port-forwards checked
both `pgbouncer-rw` and `pgbouncer-session`. Baseline startup reached an
authentication request. Adding each of the four parameters individually made
both services return SQLSTATE `08P01`, "unsupported startup parameter".
No password was submitted, no login completed and no SQL was executed.
The live Pooler specs do not configure `ignore_startup_parameters`.

The exception is still needed. Transaction pooling also lacks the required
[LISTEN/session-lock semantics](https://www.pgbouncer.org/features.html).
Keep the existing direct-instance 5432 selector and database configuration.
A future session-pooler migration needs parameter handling, timeout semantics,
startup migrations, notification delivery and lock-lifetime tests. Merely
changing the host is insufficient. The probe did not test the known same-node
PostgreSQL VIP datapath issue; that remains an explicit audit prerequisite.

## Validation and audit gate

The owner also requested a full tools/features pass. All six live read-only MCP
tools were used against this repository:

| Tool | Use in this review |
|---|---|
| `list_findings` | Read all four open Kubernetes security findings; compare their paths/claims to current files |
| `common_findings` | Establish the historical population: 14 security findings, nine addressed, one dismissed, four open |
| `get_pull` | Check review/head provenance and merged state for the two network-policy findings |
| `get_review` | Read full findings and context rather than infer approval from a check status |
| `list_rules` | Verify loaded context and rules, including `verify-effective-behavior` |
| `list_followups` | Sample ten recent follow-ups for answered/ignored status; this was not a complete history export |

The open Cilium-auto finding concerns an older chart upgrade; this draft owns
explicit CNPs and checks the DNS proxy. The historical Satisfactory FRM/world
finding is already fixed in current manifests: world excludes 8080. The other
two findings concern an informational shell-safety change and a removed workload.
No finding was dismissed or altered remotely. Historical reviews do not approve
these unpublished draft commits.

The pinned source and rendered chart were also checked for feature dependencies:

| Feature | Network/deployment requirement retained |
|---|---|
| Read-only MCP, dashboard, API tokens | Internal gateway to server 8080; MCP uses bearer tokens, not dashboard sessions |
| GitHub hooks, polling, status/review publication and follow-up replies | External hook route to 8080; server HTTPS and DB; publication retries need no new runner grant |
| Review, follow-up and index Jobs | Server API access; gVisor runner template and chart admission controls remain enabled |
| Git/gh/curl, repository/OCI fetches, Aqua tools and repository commands | Runner gateway 8082; server fetches permitted public HTTPS; preserve application-level host checks |
| Similar-code search, indexing and embeddings | Runner DB/gateway; server agentgateway 4000 and DB; no runner model credentials or direct model connection |
| Tool download cache | Existing filesystem emptyDir; no S3 endpoint or extra port needed |
| Leader locks, notifications, startup migrations and review state | Direct PostgreSQL exception above; include cold startup and failover in audit |
| Metrics, dashboards, confidence scoring and review limits | vmagent 8081; configured policies remain unchanged; a successful/skipped check alone is not complete review evidence |

The MCP endpoint intentionally omits run transcripts, raw output, model usage
and refused-host events. Use existing authenticated dashboard analytics, queue,
metrics and logs for those audit checks. In particular, classify refused hosts
before expanding `config.egress.allow`, verify incomplete/canceled review
states, and compare reviewed commits with the proposed head before relying on
Kritika's review. The gateway's HTTP cache rewrites eligible requests to HTTPS;
it does not justify opening server TCP/80. No additional feature, credential or
remote command was enabled as part of this inspection.

One capability gap deserves follow-up: current OCIRepository sources include
`quay.io`, `mirror.gcr.io`, `gsoci.azurecr.io` and
`registry.erwanleboucher.dev`, absent from Kritika's application host allowlist.
The configured `flate v0.6.5` tool or `fetch_oci` can need these for chart
inspection. Confirm attempted hosts and any auth/blob redirects in refused-host
analytics before adding precise entries. A bounded read of 1,705 current server
log lines found no refusal messages; that does not replace the database-backed
analytics or prove the tools fetched those registries successfully. Review
summaries must disclose unavailable tools: a completed review can continue after
tool setup fails. Future configuration edits can use Kritika's native
`config check` against the rendered ConfigMap before rollout.

The required `validate-pr.sh` passed flate, shellcheck, secret guardrails and
24 regression tests. Local checks covered the live Cilium CRD schema, every
subject/peer selector, allowed/denied server and runner paths, and fallback
ports. Rendering pinned chart 0.0.109 with the proposed values emits no chart
network policies, eliminating the additive rules being replaced.

At least seven days of audited review Jobs, cold startup/migrations, GitHub
hooks, gateway downloads, model calls, database failover and DNS lookups are
required. Verify the evaluator models Cilium entities and DNS restrictions;
an unsupported dimension cannot produce a clean verdict. Record each legitimate
WouldDeny and its evidence before an owner-approved enforcement change.

Next phase requires owner confirmation. Suggested continuation: "Prepare the
Kritika audit proposal from this draft; keep deployment gated on my approval."
