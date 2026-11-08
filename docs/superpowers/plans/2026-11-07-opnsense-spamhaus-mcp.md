# Upstream-only reduction — PR #6666

## Current local scope

User approved abandoning the custom monitor image on 2026-11-08. The prior full
monitor specification and publication attempt are superseded, not retained as
active requirements. Branch: `docs/opnsense-spamhaus-mcp-plan`; branch/worktree
checked, clean initial status, `git pull --ff-only` up to date before edits.

- Remove this PR's complete tracked `docker/opnsense-mcp/` tree and
  `.github/workflows/opnsense-mcp.yaml`.
- Replace the large Hermes analyzer/tests with a small bounded observation script
  for three real stock tools; remove the unused baseline example.
- Replace the two planning documents with this reduced contract and evidence.
- Preserve `kubernetes/apps/ai/toolhive/mcp/opnsense-secret.sops.yaml` byte-for-byte,
  encrypted and now registered only in local Kustomization. No account/key
  regeneration or secret decryption.
- Stock MCPServer, backend/unified allowlists and Cilium controls are local only.

## Verified upstream evidence

- Docker Hub `uhlenheide/opnsense-mcp-server:0.4.2` and `latest` resolved to index
  `sha256:b6d7f23acc60796756b97b6a45fd665d5c866c1f97a0b8d1e4a0266cef9145fb`.
  Tag timestamp: 2026-11-03. Upstream README identifies this as the official image.
- Runnable platform is **linux/amd64**; child manifest
  `sha256:68450a5c1a750c402c9fc9a01be0139e26f37017ef23cab9bd6eaee4ac635171`.
  The other index entry is an attestation, not another architecture.
- Retrieved BuildKit SLSA provenance identifies source
  `https://github.com/lucamarien/opnsense-mcp-server`, revision
  `e8e24749470c5d3e6419e5322f698eeee920396d`, build completion
  `2026-11-03T18:13:47Z`. This is provenance presence, not a verified publisher
  signature or a reproducible-build claim.
- Pulled and ran the exact upstream image unchanged with network disabled,
  read-only root, dropped capabilities and UID/GID 65532. Its catalog has 81 tools,
  including `opn_list_firewall_aliases`, `opn_list_firewall_rules`,
  `opn_firewall_log`. The image API client source SHA-256 matches the pinned source:
  `bde2cc7bb04af4531bca1672bf2fad68b107d259c65626c013f165732ad826f6`.
- Synthetic HTTP transport test inside the unchanged image observed, in order:
  `/api/core/firmware/status`, `/api/firewall/alias/search_item`,
  `/api/firewall/filter/search_rule`, `/api/diagnostics/firewall/log`.
  A synthetic firmware 403 stopped all later requests. No firewall credentials
  were used. The three read routes match earlier installed-source/direct API
  verification; this does not establish stock authenticated live acceptance.
- Firmware permission was missing from the original seven grants. Earlier ACL
  probes incorrectly omitted the required leading slash; those false results
  alone were not valid authorization evidence. After explicit approval, native
  locked transaction added only `page-system-firmware-manualupdate`, validating
  the original exact set and account restrictions before serialization/save.
  A fresh model verified exactly eight direct/effective privileges, UID 2004,
  no groups/shell/authorized keys or `page-all`. Correct ACL query using
  `/api/core/firmware/status` returned true. No firmware action or key access.
- Published MCPServer v1beta1 JSON schema accepts `podTemplateSpec` with preserved
  fields and describes it as a PodTemplateSpec. Planned hostname pin belongs at
  `spec.podTemplateSpec.spec.hostAliases`. Live CRD inspection additionally
  confirmed this field and `toolConfigRef`/Secret references; local rendering and
  embedded PodTemplate schema validation passed. Actual pod/TLS testing is gated.

## Focused validation and next gate

Fourteen focused tests pass, including silent healthy runs, deduped alerts and
recoveries, unavailable evidence preserving configuration incidents, and unknown
logs not blocking valid configuration recovery. Version-2 state contains only
incident booleans and uses a lock/atomic replace; old analyzer state is rejected
rather than implicitly migrated. Global collection deadline remains 120 seconds.

Local Kustomize render and schemas passed for MCPServer, MCPToolConfig, both
Cilium policies, unified VirtualMCPServer and embedded Kubernetes PodTemplate.
Python meta-schema checking rejected a Go-only regex on an unused Cilium field;
instance validation passed using the schema validator directly (actual instance
patterns retained). No kubeconform binary was available in this session. Parent
independently reran the 14 monitor tests and full
`mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh`: all passed,
including Flux, shell and 26 regression checks. That evidence is reused here;
the full suite is not rerun for commit/push.
Focused content guards passed all eight changed/new retained files; whitespace
passed and ciphertext matches HEAD byte-for-byte. The user subsequently approved
committing/pushing the upstream-only revision and updating draft PR #6666.
Custom-image publication and its GHCR authentication blocker are no longer relevant.

Live peer pods confirmed `toolhive-name` on proxy/backend, `toolhive-basename`
only on the stdio backend, unified identity `toolhive-name=unified`, proxy port
8080 and operator-provided `/health` probes. Live ai policies contain no egress
allows; clusterwide policy inspected is DNS ingress replies only. The new scoped
ingress denies override broad namespace allows, permit only unified pod identity
and node-local health access, and exclude arbitrary direct-backend consumers.
Backend egress is limited to firewall 443 and kube-dns 53, without breaking proxy
Kubernetes-API attach. Schema/selector intent is not proof of dataplane behavior.

Before deployment: separate explicit merge/apply approval. After approved
rollout: verify dual filtering/result shape (known proxy resultType caveat),
denied tool calls at dispatch only, denied non-unified proxy access, backend egress,
amd64/gVisor lifecycle, hostAliases TLS and authenticated stock reads. Then obtain
separate Hermes installation/cron approval: every 15 minutes, `--no-agent`,
`--catch-up off`, initially paused, alert/failure delivery to
`discord:1509648802678702203:1531266425594056777`. Verify silent healthy output and
one alert/recovery pair before any resume. No current cron or runtime install.
Keep PR #6666 draft. Push/description approval does not authorize merge, deployment,
account changes, publication or Hermes installation/enablement.
