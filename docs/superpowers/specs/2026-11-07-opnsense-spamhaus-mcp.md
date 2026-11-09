# Limited upstream OPNsense observations

## Approved scope (2026-11-08)

Use the maintained upstream image unchanged. No fork, image build, patch, overlay,
custom six-tool API or publication workflow. This supersedes the former full
Spamhaus monitor design. Prefer explicit limited coverage over a complex analyzer.

Candidate image:
`docker.io/uhlenheide/opnsense-mcp-server:0.4.2@sha256:b6d7f23acc60796756b97b6a45fd665d5c866c1f97a0b8d1e4a0266cef9145fb`

The official image is on Docker Hub; source is
`https://github.com/lucamarien/opnsense-mcp-server` at
`e8e24749470c5d3e6419e5322f698eeee920396d`, not an uhlenheide GitHub repository.

## Observations, not enforcement or health guarantees

The small Hermes script uses only these stock tools:

| Tool | Intended observation |
| --- | --- |
| `opn_list_firewall_aliases` | Matching alias definitions in a complete bounded search |
| `opn_list_firewall_rules` | Matching rule descriptions in a complete bounded search |
| `opn_firewall_log` | Counts in a recent general firewall sample, without raw traffic output |

Calls request at most 100 returned rows. Incomplete configuration search results
are unavailable, never proof of absence. The stock log tool fetches the backend
log response before client-side truncation: the limit is not a server-side bound.
Samples have no guaranteed interval, continuity, deduplication or rate. Neither
logs nor aggregate Prometheus metrics are attributed to Spamhaus. Existing general
firewall health monitoring remains separate; no new Prometheus integration is
needed for this reduction.

No live table count, effective PF-policy validation, per-rule counters, hit-rate
thresholds or daily feed refresh success. Configuration presence does not establish
any of those. A minimal version-2 incident file deduplicates configuration and
collection alerts and recoveries only: exactly one named alias and three enabled
marked rule definitions are expected. Unavailable configuration never clears its
incident; log collection uncertainty is separate and never malicious/healthy traffic.
Successful log collection recovery means only that collection resumed.

Retain the approved **15-minute, silent-healthy, alert/recovery-only** behavior.
Healthy/unchanged runs emit zero bytes. No periodic observation dump. No baseline
file, script, state or cron is installed in this task. The existing script-based
architecture stays; do not substitute an agent prompt or enable a schedule.
The intended script path is `/opt/data/scripts/opnsense_spamhaus_watch.py`; state
is `/opt/data/cron/state/opnsense-spamhaus-watch.json` with a sibling `.lock` file.
Provision the state directory as the Hermes user only during approved installation.
State version 2 stores only incident booleans; incompatible state needs deliberate
migration. No baseline file is used.

## Authorized account prerequisite

Stock 0.4.2 lazily calls `/api/core/firmware/status` before its first API operation.
An HTTP denial aborts the requested read; there is no supported version override.
On explicit authorization, added only `page-system-firmware-manualupdate` (System:
Firmware) to the seven prior privileges. Native persisted-model verification shows
exactly eight direct/effective privileges, no groups/admin/shell/authorized keys.
ACL evaluation authorizes `/api/core/firmware/status`; no firmware API operation,
credential read, key generation or decryption was performed. This is ACL evidence,
not an authenticated stock-tool smoke test.
The existing account is `hermes-spamhaus-monitor` (UID 2004); its credential is
already encrypted in `kubernetes/apps/ai/toolhive/mcp/opnsense-secret.sops.yaml`.
Do not recreate the account, API key or Secret. Any rotation requires separate approval.

Local MCPServer, dual allowlists and Cilium policies are prepared and registered
along with the unchanged encrypted Secret. Nothing has been applied. The stock
backend does not exist live yet; live acceptance remains required.

## Requirements before any later deployment

- Separate approval for any further account expansion and for deployment. Retain
  `user-config-readonly`, no groups/admin/shell/authorized keys.
- Use only the immutable upstream image above on amd64, with TLS verification,
  `OPNSENSE_ALLOW_WRITES=false`, `OPNSENSE_ALLOW_SENSITIVE=false`, and credentials
  from the existing encrypted Secret. No decryption in this task.
- ToolHive backend allowlist and unified aggregation must expose only the three
  named tools, not the stock catalog of 81 tools. Exclude arbitrary APIs, packet
  capture, config backup and all writes. Deny tests must stop at tool dispatch.
- Bind `opnsense.${SECRET_DOMAIN}` to `${OPNSENSE_IP}` (currently `192.168.0.1`) using pod
  `hostAliases` while retaining HTTPS hostname verification. The published
  MCPServer schema accepts `spec.podTemplateSpec` as a preserved PodTemplateSpec;
  installed CRD and local PodTemplate schema checks passed; verify the actual
  operator-created pod and TLS connection after authorized rollout. Never
  use an invented upstream version/API-style environment variable or change DNS.
- Follow peer resource/probe/security patterns. Restrict egress to firewall
  TCP/443 and required DNS; verify direct-backend isolation against existing
  additive network policies. Do not assume an extra allow policy enforces denial.
  Preserve the clusterwide same-node CoreDNS reply exception only for kube-dns
  in kube-system; even CoreDNS must remain denied on the MCP proxy port.
- Validate stock reads with the authorized account, gateway allowlist and TLS
  routing before claiming monitoring works. No live acceptance is claimed here.

The upstream-only revision was pushed previously. User approved pushing the verified
review fixes and addressing their threads. No merge, deployment, further account/DNS change,
publication or Hermes enablement is authorized. Later rollback pauses the approved monitor job
first, then reverts monitor GitOps changes; never alter Spamhaus firewall policy.
