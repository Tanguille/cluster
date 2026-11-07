# OPNsense Spamhaus monitoring: approved design

Status: local source/image and collector preparation underway; no publication,
deployment, secrets work, firewall connection or runtime installation. The installed
Hermes adapter has now been inspected read-only and bound locally; live acceptance
remains pending. Target log-time uncertainty is explicit and signal-specific. See
[local implementation notes](../../../docker/opnsense-mcp/README.md).

## Outcome and boundaries

Extend lucamarien's maintained OPNsense MCP, publish a reproducible derived image,
register it in ToolHive's unified gateway, and run a deterministic Hermes watchdog
every 15 minutes. Deliver alerts and recoveries to
`discord:1509648802678702203:1531266425594056777`; healthy runs emit zero bytes.
There is no autonomous remediation, firewall write, SSH, arbitrary command, or
arbitrary API tool. New credentials belong only to the MCP workload, never Hermes.

The user-confirmed initial target is OPNsense 26.7.8 at `192.168.0.1`, alias
`Spamhaus_DROP`, 1,853 entries, three MVC rules marked `SDROP-20261106-v1`.
Ingress source/destination quick rules cover `pppoe0`, `em0`, `wg0`; outbound
destination rules cover `pppoe0`. Do not require absent `em1`–`em3` bridge members.
Three MVC rules may expand into more PF rules. The entry count is a baseline,
not a permanent expected value. Obtain UUIDs and complete configured attributes
through the approved read path before enabling monitoring.

Git pushes, publishing an image, SOPS edits/decryption, applying/reconciling, and
runtime job installation remain explicit execution gates. Approval of this design
does not waive those gates. No exporter credentials will be reused or inspected.

## Source and packaging decision

Use upstream `lucamarien/opnsense-mcp-server` commit
`e8e24749470c5d3e6419e5322f698eeee920396d` (package version 0.4.2), retaining its
MIT license, API client, configuration, lifespan, and stdio transport. Maintain a
small reviewable patch series in `docker/opnsense-mcp/patches/`, not a separate
unpublished fork and not a replacement MCP implementation.

Follow the repository's `docker/talos-kernel/` plus GitHub Actions GHCR publishing
pattern. Build the upstream source archive at that exact commit, verify its
committed SHA-256, apply patches without fuzz, and install hash-locked Python
dependencies. Pin the Python 3.13 slim builder/runtime images by registry digest
when implementing; do not invent a digest or use the upstream floating Dockerfile.
Record source commit, archive hash, base image digests, dependency lock, local Git
revision, SBOM, and build provenance. Run as non-root with a read-only filesystem.

Publishing target is `ghcr.io/tanguille/opnsense-mcp`, tagged
`0.4.2-monitor.<cluster-short-sha>` and deployed by its resulting digest. This is a
new package, not a claim that one exists. Use a manually dispatched workflow with
`contents: read`, `packages: write`, checkout credential persistence disabled, and
the existing pinned action conventions. Obtain approval to push the build branch
and publish the package, verify package pull visibility, then insert the real
image digest in the deployment change. Never merge an unresolved image reference.
An upstream contribution can follow separately; deployment does not depend on its
acceptance. No registry login or publication occurs during planning.

## Read-only extension contract

Add `OPNSENSE_MONITOR_ONLY=true` and `OPNSENSE_API_STYLE=snake_case` to the patched
upstream configuration. Monitor mode must reject writes/sensitive mode and TLS
verification disabled at startup, register only the six tools below, and enforce
an exact method/path allowlist in the API client before making a request. Explicit
API style bypasses automatic firmware version detection: **no firmware privilege
is needed**. This is deliberate version-scoped configuration, not a version claim
from an unauthenticated response. Unknown API shapes fail closed.

The custom runtime additionally carries a root-owned read-only package marker
forcing this profile even if `OPNSENSE_MONITOR_ONLY=false` is supplied. Environment
configuration alone is not the runtime's catalog/security boundary.

All tools return JSON objects with `schema_version: 1`, bounded results, and
explicit errors. Inputs never select a host, URL, arbitrary path, or method.
Alias name is fixed to `Spamhaus_DROP` and rule marker to `SDROP-20261106-v1` in
deployment configuration. Neither is taken from log text.

| Tool | Underlying `/api/` paths | Result requirements |
| --- | --- | --- |
| `opn_monitor_alias_config` | POST `firewall/alias/search_item` | Exact name match; UUID, enabled, type, URLs, refresh interval; distinguish missing from denied |
| `opn_monitor_alias_contents(page=1, page_size=500)` | POST `firewall/alias_util/list/Spamhaus_DROP` | Upstream `total`, `current`, `rowCount`, entries; 1–500 rows/page; preserve total, never infer it from page length |
| `opn_monitor_table_metadata` | GET `firewall/alias/get_table_size` | Only target table's counters, count hint, content mtime, global capacity; mtime is explicitly not refresh success |
| `opn_monitor_rules(page=1, page_size=100)` | POST `firewall/filter/search_rule` | Upstream totals and full MVC fields; filter marker locally after bounded pagination; return rule UUIDs |
| `opn_monitor_rule_stats` | GET `firewall/filter_util/rule_stats`; GET `diagnostics/firewall/list_rule_ids` | Unwrap `stats` map; preserve UUID/hash, counters, `pf_rules`, running IDs/descriptions; no fabricated description-to-UUID join |
| `opn_monitor_logs(limit=2000, digest=null)` | GET `diagnostics/firewall/log` | 1–2,000 raw rows, raw count, oldest/newest timestamp, digest if actually supported by response, saturation flag; preserve rid/direction/interface |

Search POSTs are reads in this API, not mutations. They need explicit allowance;
"block all POST" is not the security boundary. Body keys, query keys, alias path,
and numeric bounds are validated. Reject redirects and never include authorization
headers, request credentials, or raw upstream HTML in logs/errors. Bound each HTTP
response to 8 MiB and request duration to 15 seconds; report overflow as unavailable.
Bound one watchdog snapshot to 120 seconds, 100 alias pages and 50 rule pages.
Changed totals, duplicate pages, unsupported fields, and exhausted bounds produce
incomplete evidence, never a healthy verdict. Check totals again at end; PF tables
are not atomic snapshots, so report inconsistencies rather than claiming atomicity.

## Evidence semantics

- Live alias count comes from the paginated PF table, with IPv4/IPv6 breakdown.
  `get_table_size.count` is a hint: upstream takes the maximum of intended cached
  count and live count. It is not the exact PF count.
- `get_table_size.updated` is `/var/db/aliastables/<name>.txt` mtime. The updater
  writes only changed contents. An unchanged successful daily refresh can leave
  it old. Never alert "refresh failed" from mtime alone.
- The first delivery states that daily download success is **not observable** via
  these endpoints. Check enabled feed configuration, populated live table, family
  coverage and cardinality changes. Expose `refresh_success: unknown` in state.
  Do not silently promote this to verified freshness. Verified last-success
  telemetry is a separate future enhancement, not a hidden SSH escape hatch.
- Rule-statistics counters are aggregated by stable rule UUID/hash; resets or
  changing `pf_rules` establish a new baseline, not a negative rate or recovery.
  Runtime IDs plus counters prove rule presence, not every compiled PF attribute;
  compare configured semantics and keep that distinction in reports.
- Logs are recent bounded samples, not a complete archive. A saturated window
  whose oldest row is newer than the previous successful poll is a coverage gap.
  Missing logging, timestamp ambiguity, or unresolved rule identity is unknown.
  Aggregate blocked counters cannot attribute Spamhaus traffic.
  A gap never advances the confirmed log head. A separate pending anchor from
  one complete, unsaturated window may establish a new baseline only when a later
  complete window overlaps it. Recovery names that new baseline and explicitly
  leaves the previous gap unknown; missing intervals never enter rate history.

## Monitor behavior and persistence

Repository-owned Python collector/analyzer runs in Hermes's default profile via
its existing ToolHive client, with `find_tool` discovery and then `call_tool`.
No direct firewall or Prometheus connection from Hermes. Stage only approved
scripts, under the non-root Hermes identity, after tests and live verification.

State at `/opt/data/cron/state/opnsense-spamhaus-watch.json` holds schema version,
timestamps, approved rule baseline, previous counter samples, up to 96 complete
log-rate samples, active incidents and dedupe information. Atomic writes and a
single-run lock prevent overlap; invalid state is an explicit monitor failure.
Keep no credentials or full traffic logs. Bound retained source/destination
summaries; omit payloads and DNS contents from Discord.

Initial thresholds are conservative and explicit:

- Alert immediately on missing/disabled alias, empty table, loss of a previously
  present IP family, or missing/mutated marked rule. Compare all approved rule
  fields, not merely the description. A count fall of more than 50% from the
  approved initial baseline is a warning, not an instruction to restore the list.
- Report a positive outbound marked-rule packet delta on a new incident; logs may
  enrich it only with verified rule IDs. Describe "unusual / worth checking",
  never "malicious". Repeated unchanged incidents remain silent; after four
  complete zero-delta polls report recovery. Counter resets do not count as zero.
- With at least eight complete historical samples, warn on log volume exceeding
  five times the rolling median and at least 1,000 new rows per poll. On saturation
  report a coverage/volume warning instead of an invented exact rate.
- Collection errors, denied calls, malformed responses and missing time coverage
  are separate incidents. Complete evidence for the relevant signal is required
  for recovery; partial evidence for that signal cannot clear its incident.
  Unknown logs do not block recovery of independently complete table/rule signals
  or four complete zero-delta outbound counter polls. Log incidents require their
  own complete evidence. Repeated failures are deduplicated, not
  silently labelled healthy. Routine healthy stdout is exactly empty.

Hermes schedule: `every 15m`, `--no-agent`, `--catch-up off`, explicit Discord
delivery above, failures delivered to the same thread. Create paused; verify a
manual run and scheduler delivery before resuming. Existing cron-health monitoring
must include the new job so a stopped scheduler does not look healthy.

## Transport, network and secrets

Use stdio MCPServer `opnsense` in namespace `ai`, group `all`. ToolHive provides
the proxy transport. Add the same six-tool `MCPToolConfig` allowlist to unified
aggregation. The server itself registers no other tools: unified filtering alone
does not secure direct backend access.

The existing namespace ingress allowlist permits intra-`ai` traffic, and allows
union. A new narrower allow policy alone is ineffective. Add a scoped Cilium
**deny** policy excluding all backend clients except the verified unified workload
identity and explicitly required probe/scrape identities; confirm actual labels,
ports, operator/proxy topology and health behavior before enforcing it. No new
HTTPRoute. Keep existing unified access behavior unchanged; it is anonymous and
network-controlled, not per-Hermes authenticated.

Restrict the backend's egress to `192.168.0.1:443` plus cluster DNS. Disable service
account-token automount, retain inherited gVisor, drop capabilities, prohibit
privilege escalation, set explicit CPU/memory requests/limits. Do not reuse another
MCP's selectors without checking rendered/live identity. Use existing proxy health
mechanisms, not a fabricated HTTP health endpoint on a stdio process.

TLS target is `https://opnsense.${SECRET_DOMAIN}/api`. On 2026-11-07 a credential-free
handshake to `192.168.0.1:443` with that host's actual SNI verified hostname and
system trust successfully; DNS resolved to `192.168.0.1`. Certificate issuer was
Let's Encrypt YR2, valid 2026-10-28 through 2027-01-26. Reverify from the deployed
MCP runtime: local trust is not proof of container trust. No custom CA or TLS bypass
is presently needed. The existing exporter explicitly disables verification;
that setting is not precedent for this deployment.

## Manual API-user setup: next user action

1. In OPNsense **System → Access → Users**, create `hermes-spamhaus-monitor` with
   no administrator group and no shell/SSH access. Use a generated password kept
   in your password manager; no password or API key goes in chat.
2. Add these effective privileges only (check inherited privileges as well):
   `System: Deny config write`, `Firewall: Aliases`,
   `Diagnostics: PF Table IP addresses`, `Firewall: Rules [new]`,
   `Firewall: Rules`, `Diagnostics: Firewall sessions`,
   `Diagnostics: Logs: Firewall: Live View`.
   Do not add `Firewall: Alias: Edit`, Show States, backups, firmware, or full admin.
   Some page privileges include writes; server allowlisting and deny-config-write
   are complementary controls, not a claim of endpoint-granular built-in ACLs.
3. Generate a new API key for this user; keep the downloaded `apikey.txt` in a
   protected local location/password manager, not this repository or a chat upload.
   Tell the parent only that the user/key is ready and confirm the effective
   privilege names. Do not paste the file or values.
4. When the implementation branch contains the reviewed secret template, **you**
   enter the two values using `mise exec -- sops kubernetes/apps/ai/toolhive/mcp/opnsense-secret.sops.yaml`
   in a trusted local terminal/editor. The resource will be Secret `opnsense-mcp`, namespace `ai`,
   with `stringData.OPNSENSE_API_KEY` and `stringData.OPNSENSE_API_SECRET`.
   Only ciphertext is saved/staged. Follow [the SOPS runbook](../../sops.md).
   Do not pass secrets as shell arguments, environment exports, or kubectl literals.
   If the agent is to edit/encrypt instead, explicitly authorize that separate
   operation first; planning does not authorize opening private identities.

The user can complete steps 1–3 now. Step 4 waits for the reviewed template; no
empty or placeholder Secret should be applied. This flow does not require sharing
credentials with the agent, and never grants them to Hermes.

## Acceptance and rollback

Enable only after: fixture tests pass; actual published image digest is verified;
manifests render; intended read tools work with the dedicated user; forbidden
tools/methods cause zero upstream requests in tests; direct backend bypass fails;
live table/rules agree with the approved baseline; truncated logs are reported as
unknown; a Hermes test alert and recovery arrive once in the approved thread.

Rollback first pauses the Hermes job. Revert the MCP deployment change through
GitOps with approval; revoke the dedicated OPNsense key if needed. Never roll back
the firewall's Spamhaus rules as part of monitor rollback. Preserve state for
diagnosis without retaining credentials. No promise of monitoring until enabled
and verified.

## Source references

- [Pinned upstream server and client](https://github.com/lucamarien/opnsense-mcp-server/tree/e8e24749470c5d3e6419e5322f698eeee920396d/src/opnsense_mcp)
- [OPNsense 26.7.8 controllers](https://github.com/opnsense/core/tree/96978001a8343669154e4492172ce0a07dac7e4b/src/opnsense/mvc/app/controllers/OPNsense)
- [OPNsense table metadata, counters, updater](https://github.com/opnsense/core/tree/96978001a8343669154e4492172ce0a07dac7e4b/src/opnsense/scripts/filter)
- [Core privilege definitions](https://github.com/opnsense/core/blob/96978001a8343669154e4492172ce0a07dac7e4b/src/opnsense/mvc/app/models/OPNsense/Core/ACL/ACL.xml)
- [MVC rule privilege definitions](https://github.com/opnsense/core/blob/96978001a8343669154e4492172ce0a07dac7e4b/src/opnsense/mvc/app/models/OPNsense/Firewall/ACL/ACL.xml)
- [Implementation plan](../plans/2026-11-07-opnsense-spamhaus-mcp.md)

The previously cited `08dc30b62227af4e488c93f63af1344a79343b8d` is the annotated
26.7.8 tag object, not a source commit; the links above use its peeled commit.
Source inspection also confirms `rowCount` is returned-row count, not requested
page size. Final-page and empty-page fixtures preserve that distinction.
