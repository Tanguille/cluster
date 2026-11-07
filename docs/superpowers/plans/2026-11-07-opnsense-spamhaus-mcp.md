# OPNsense Spamhaus MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver evidence-bound, read-only Spamhaus monitoring through ToolHive and Hermes.

**Architecture:** Patch the pinned maintained lucamarien server with a restricted monitor profile and six bounded tools. Build/publish a source-derived image, isolate its backend, and run a deterministic collector/analyzer through the existing unified gateway.

**Tech Stack:** Python 3.13, upstream FastMCP/httpx, pytest/respx, Docker/GHCR, ToolHive CRDs, Flux/Kustomize, Cilium, Hermes script-only cron.

**Spec:** [Approved design](../specs/2026-11-07-opnsense-spamhaus-mcp.md).

## Local execution record — 2026-11-07

User authorized prepare-code-first only. Existing isolated branch was verified;
plain pull had no upstream, so `git pull --ff-only origin main` fast-forwarded
from `90449787c` to `5f0b2dddb`, preserving the untracked planning documents.
No commit, push, publication, SOPS operation, firewall connection, live apply or
runtime install was performed. No active Kustomization registration was added.

- Task 1: local pinned source/patch/dependency locks, Docker test/runtime targets,
  fixtures and security tests implemented. Initial unpatched test run: **19
  failures**, expected missing monitor profile. Final focused host and container
  suites: **754 passed** (732 upstream + 22 monitor tests). Runtime stdio smoke
  passed initialize, exact six-tool catalog, denied unknown tools, bounds and
  sanitized unavailable read with network disabled/read-only root. UID 65532 and
  absence of git/compiler checked. No live successful tool call is claimed.
- Task 2: manual test-only-by-default workflow prepared; explicit `publish=true`
  is a later approval gate. `actionlint` 1.7.12 passed. No GHCR artifact/digest
  exists from this work; local images are not deployment references.
- Task 3: collector, pure analyzer, state/lock/atomic-write helpers, CLI skeleton
  and synthetic baseline created. Initial missing implementation: **12 failing
  tests**. First-pass unittest run: **15 passed**, plus `py_compile`. Three subsequent
  state/continuity regressions were observed failing, then fixed. Healthy fixture
  output is zero bytes. The first pass deliberately left runtime adapter binding
  incomplete; the continuation below resolves that gap through read-only runtime
  inspection, rather than assuming local filesystem availability was the limit.
- Ruling: use peeled OPNsense commit `96978001a8343669154e4492172ce0a07dac7e4b`;
  the prior reference was an annotated tag object, causing raw-source 404s.
- Ruling: preserve actual returned `rowCount`, not requested size. Source-shaped
  corrected tests failed (3 MCP tests and one pagination collector test), then
  passed after fixing both boundaries. This is verified against
  `ApiControllerBase::searchRecordsetBase` at the pinned target commit.
- Target `read_log.py` removes positive timezone offsets and emits naive RFC3164
  times. Unknown log coverage remains explicit. The parent clarified independent
  per-signal recovery in the continuation below; no timezone assumption was added.
- Full repository validation and independent review remain the parent's work.
  Nested reviewer dispatch was unavailable (subagent depth limit). The test matrix
  is not yet exhaustive for every planned adversarial case; unchecked task items
  below are not a claim of complete acceptance.

See [build and handoff details](../../../docker/opnsense-mcp/README.md). Local test
logs and prepared source are retained in the approved external scratch directory
`/home/tanguille/.cache/opencode-tmp/opencode/opnsense-monitor-local-preparation`.

### Continuation: runtime adapter and per-signal recovery

- Verified branch/status and pulled `origin main --ff-only` again: already current.
- ToolHive `find_tool` discovery returned logs/metadata tools, no suitable pod
  exec/read tool. Used read-only `kubectl exec -n ai deploy/hermes -c app` to inspect
  AST-selected source from `/opt/data/.hermes/scripts/toolhive_retry.py`. No runtime
  writes, credential values/config dumps or firewall connections occurred.
- Bound the installed async discovery tuple contract and raw backend-call helper.
  Exact expected prefixed names must be discovered, with no substring matching.
  The raw helper checks outer MCP errors that the installed SDK call helper loses.
  Added sanitized envelope decoding, lazy discovery inside the snapshot deadline,
  zero retry sleeps, cached names, and silent adapter diagnostics.
- Actual transport checks: installed SDK discovery returned a generic TaskGroup
  failure; a read-only raw metrics call raised `RuntimeError`. Error bodies were
  suppressed. Source/import contract is verified; successful live monitor calls
  are not claimed and remain a Task 5 acceptance gate.
- Parent-approved clarification: table/rule recovery uses complete relevant
  evidence independently of log coverage. Outbound recovery uses four complete
  zero-delta counter snapshots, not log timestamps. Logs never recover or update
  rate history without their own complete evidence. No timezone is inferred.
- Added tests for exact/duplicate/hostile-name discovery, nested/error/oversized
  envelopes, actual signal deadline interruption, per-signal recovery and withheld
  recovery, partial log-call failure, reset/missing counter streaks, timestamp
  ambiguity/identity/order/saturation, digest continuity at equal timestamps,
  main CLI zero-byte healthy output, deduplicated collection failure, state
  corruption/atomic overflow, repeated clock rollback and newly observed family
  loss. Counter zero-PF-rule and invalid-type cases fail closed.
- Red/green evidence: adapter/signal tests initially had 3 failures and 7 errors;
  later adversarial tests exposed 3 failures (zero PF-rule count, equal-timestamp
  counting, oversized state); final state tests exposed 7 failures including
  repeated clock rollback. All were corrected with focused tests.
- MCP tests now also cover 401/403/500 sanitization, transport timeout and explicit
  API-style no-firmware probing. Focused final verification: **759 passed** in
  the Docker test target (732 upstream + 27 monitor tests); **34 Hermes unittests
  passed**, `py_compile`, actionlint 1.7.12 and focused whitespace checks passed.
  Logs are `adapter-build-test.txt` and `adapter-final-unittest.txt` in the scratch
  directory above. No publication, live installation or repository-wide validation
  was attempted. All implementation files remain uncommitted for parent review.

### Independent review disposition

Reviewed the actual pinned OPNsense/controller/script source and current local
implementation before changes. Fast-forwarded the isolated branch to `6b30131f1`
with untracked implementation preserved. No repository-wide validation was rerun.
The parent's Flux/shell and 24 secret-check test results are parent-reported,
not newly claimed execution evidence here.

1. **Fixed legacy collisions.** `FilterController::searchRuleAction` always merges
   `filter list non_mvc_rules`; that script maps `label` to potentially repeated
   `uuid` and assigns unique `sort_order` from priority/sequence. Legacy pagination
   now uses that source-defined key, checks duplicate keys and compares the full
   first page again. MVC/marked identities remain strict; duplicate MVC and legacy
   marked rows fail closed. Tests exercise collisions spanning two pages.
2. **Fixed signal isolation.** Collection has bounded per-signal failure handling.
   Alias/table/rules/counters continue independently after another signal fails.
   Logs use independently validated running IDs; they do not require configured
   rule collection or counter-value parsing to succeed. A global timeout still
   terminates the snapshot rather than restarting a timer for each signal.
3. **Fixed gap bookkeeping.** Incomplete continuity never advances the confirmed
   digest. A separate pending anchor requires a subsequent complete overlapping
   interval to re-baseline. Invalid/saturated windows cancel that pending interval.
   Recovery explicitly says the previous gap remains unknown; gap intervals never
   contribute to rate history. Tests prove recovery is possible without erasing
   unknown history or converting multi-poll gaps into one poll's exact count.
4. **Added production-path volume test.** Real collector log rows, timestamps,
   digests and successive polls produce seven samples, then a non-alerting 1,000-row
   eighth sample, then an alerting 1,000-row ninth sample above five times median.
   This supplements—not replaces—the pure analyzer threshold unit tests.
5. **Added six tool descriptions.** Catalog descriptions identify read-only scope,
   fixed alias, pagination/log bounds, metadata freshness limitations and identity
   semantics. Container smoke asserts meaningful descriptions on the exact catalog.
6. **Verified adapter signature; no live-success claim.** Read-only AST inspection
   reconfirmed `call_toolhive(method, params=None, max_retries=3, backoff=5)` and
   `_raw_call_tool(tool_name, params)` at installed source SHA-256
   `d99ee24bc6860af0a5e6edb9bf0dcc64211a1abd28c3a7a4eb765867576173cc`.
   Our positional mapping is correct. No new live tool invocation was needed.
7. **Strengthened boundary tests.** Public generic `get`/`post` reject even otherwise
   allowlisted logical routes before HTTP. Synthetic fixtures now cover all seven
   exact paths/methods, legacy rows and naive log timestamps. Invalid log limits
   reject before HTTP; a 2,000-row result preserves rows, digest and saturation.
8. **Fixed runtime override escape.** A root-owned mode-0444 package `MONITOR_ONLY`
   marker forces restricted config/registration regardless of the environment.
   Unrestricted direct client construction is rejected in that image. The source
   remains optional-profile for upstream regression tests; the published runtime
   target always includes the marker. Smoke sets `OPNSENSE_MONITOR_ONLY=false`
   and proves the catalog stays six tools, unknown calls fail, and marker/config
   invariants hold. Marker permission verification initially failed and was fixed
   by explicit Docker `COPY --chmod=0444`.

Focused verification after fixes: **40 Hermes unittests passed**, **35 monitor
pytest tests passed**, Python compilation passed, exact patch application passed,
runtime image built, and restricted container smoke passed. Upstream/full repository
suites were deliberately not rerun per the parent's request. Initial review tests
showed 7 failures/1 error plus failed override smoke before fixes; the additional
interrupted-rebaseline regression failed before its fix. Evidence files are
`review-final-unittest.txt`, `review-final-pytest.txt`, `review-build-runtime.txt`
and `review-final-smoke.txt` in the scratch directory above. All changes remain
local/uncommitted; no publication, credentials, deployment or runtime installation.

## Global Constraints

- Upstream source: `e8e24749470c5d3e6419e5322f698eeee920396d`, version 0.4.2; target OPNsense 26.7.8.
- No firewall writes, arbitrary API/command/SSH tools, exporter credential reuse, or secrets in Hermes/chat/logs.
- `OPNSENSE_MONITOR_ONLY=true`, `OPNSENSE_API_STYLE=snake_case`; no firmware privilege.
- TLS verified at `https://opnsense.${SECRET_DOMAIN}/api`; never disable verification.
- Schedule `every 15m`, no agent, catch-up off; delivery `discord:1509648802678702203:1531266425594056777`.
- Healthy stdout is exactly empty; alert/recovery and incomplete evidence are explicit.
- Push, image publication, SOPS work, live apply/reconcile and runtime installation are separate approval gates.
- This document authorizes no implementation or deployment by itself; parent resumes work after planning handoff.

## Review Focus

- API pagination changes mid-read must yield incomplete evidence, not a fabricated healthy count (Task 1).
- A reload between polls must not produce a negative delta or false recovery (Task 3).
- Busy logs and unparseable timestamps must not imply zero outbound events (Tasks 1 and 3).
- Alternate transport/direct-backend access must not expose forbidden tools (Tasks 1 and 4).
- Restart, damaged state or overlapping cron runs must not lose active incidents silently (Task 3).

---

## Task 1: Source-derived restricted MCP image

**Create:**

- `docker/opnsense-mcp/Dockerfile`
- `docker/opnsense-mcp/upstream.lock.json` — upstream commit/archive hash and base-image digests
- `docker/opnsense-mcp/requirements.lock` — exact dependencies with hashes
- `docker/opnsense-mcp/patches/0001-monitor-profile.patch`
- `docker/opnsense-mcp/tests/test_monitor.py`
- `docker/opnsense-mcp/tests/fixtures/26.7.8.json` — synthetic source-shaped fixtures, no live personal logs
- `docker/opnsense-mcp/README.md` — build, provenance, upstream rebase and tests

Patch upstream `src/opnsense_mcp/{config,api_client,server}.py` and add
`src/opnsense_mcp/tools/monitor.py`. Keep those changes in one patch rather than
vendoring the whole upstream source. The patch must fail to apply if the source
has drifted. Preserve upstream license and version metadata with a local build label.

**Interfaces:** Six `opn_monitor_*` tool names, parameters and JSON schemas defined
in the spec. Internally use `request_monitor(endpoint: str, *, page: int = 1,
page_size: int = 500, limit: int = 2000, digest: str | None = None) -> dict` on the
upstream API client; `endpoint` is an internal enum-like key, never an MCP input.
Explicit API style bypasses version probing. Server registration in monitor mode
imports only `tools.monitor`; reuse upstream lifespan/client, not a second server.

- [ ] Record archive SHA-256 and resolve actual Python 3.13 slim registry digests; generate hash-locked dependencies and store provenance in `upstream.lock.json`. Do not use a package release as proof of image contents.
- [ ] Write failing tests: nested `status/stats` UUID map preserves counters and `pf_rules`; 1,853 and 10,001 entries paginate completely; page size above 500 and log limit above 2,000 reject; total changes/duplicate pages are incomplete; absent target differs from denied; unknown shape raises a sanitized error.
- [ ] Add failure tests for old unchanged content mtime (`refresh_success` remains unknown), metadata count differing from PF count, saturated logs, no timestamp/digest, unknown rule identity, and IPv6 entries. Assert the tool never calls a refresh/update endpoint.
- [ ] Add security tests: only six tools listed; each disallowed tool call fails before HTTP; wrong method/path, extra query keys, redirects, response over 8 MiB, writes/sensitive/TLS-off startup rejected; zero firmware-status requests in explicit-style mode. Supply synthetic credentials and assert they never appear in captured logs/errors.
- [ ] Run tests against the unpatched prepared source and record expected failures. Implement the patch minimally, then run the same tests and upstream regression suite against patched source.
- [ ] Build with pinned source/base/dependencies. Verify non-root UID, read-only-root compatibility, stdio initialize/tools-list/tools-call, exact allowed catalog and denied-call behavior in the built container. Runtime has no compiler, git, source-download credentials, or shell-based firewall integration.

Verification: document deterministic preparation/test commands in the Docker README;
the container test target must run `python -m pytest -q` against upstream tests and
`docker/opnsense-mcp/tests`. All pass before Task 2. Record actual results, not only
the commands. Local commits may be made after scoped review; never push implicitly.

## Task 2: Publish a verifiable image before referencing it

**Create:** `.github/workflows/opnsense-mcp.yaml`.

**Consumes:** Task 1 Docker build context and lock.
**Produces:** `ghcr.io/tanguille/opnsense-mcp:0.4.2-monitor.<cluster-short-sha>` and
verified immutable manifest digest, source/patch revision, provenance and SBOM.

- [ ] Add manual `workflow_dispatch` only; no PR-triggered publication. Use the existing Talos workflow's pinned checkout/mise/buildx/login conventions, `persist-credentials: false`, read contents/write packages permissions only on the publishing job, bounded timeout and concurrency.
- [ ] Build/test before publish; tag from checked-out source revision, attach provenance/SBOM, print only non-sensitive image references/digests in the job summary. Verify target architecture against intended cluster node scheduling.
- [ ] Validate workflow locally, build without publishing, and review the source/lock/patch diff. Ask approval to push this branch and publish the new GHCR package. A local image is not a deployable artifact.
- [ ] After approval, publish, confirm package pull visibility and a credential-free digest pull where public access is intended. Compare image OCI source/revision labels and provenance to the reviewed commit. Record the resulting digest for Task 4; do not invent it in advance.

No separate upstream fork/repository is required. Upstream contribution is optional
and separately authorized. Do not merge deployment manifests until the image exists.

## Task 3: Deterministic Hermes collector and analyzer

**Create:**

- `kubernetes/apps/ai/hermes/app/resources/opnsense_spamhaus_watch.py`
- `kubernetes/apps/ai/hermes/app/resources/test_opnsense_spamhaus_watch.py`
- `kubernetes/apps/ai/hermes/app/resources/opnsense_spamhaus_baseline.example.json`

These are version-controlled runtime script sources like the existing Google digest
resources, not a new privileged sidecar. Do not change the Hermes HelmRelease or
mount firewall credentials. Runtime script staging is an explicitly approved later
operation; avoid broad ConfigMap mounts over the existing PVC scripts directory.

**Interfaces:**

- `collect_snapshot(call_tool, now: datetime) -> dict` consumes discovered exact six-tool names via existing Hermes ToolHive adapter; validates schema version and bounded pagination.
- `evaluate(snapshot: dict, state: dict, baseline: dict) -> tuple[dict, list[str]]` is pure; produces new state and concise messages.
- `main() -> int` locks, reads state/baseline, collects, evaluates, atomically writes state, prints messages only. Exit zero for observed anomalies; nonzero for unrecoverable runtime failures. Recoverable collection incidents are deduplicated in state.

- [ ] Write failing unit tests for healthy zero bytes; first incident/unchanged duplicate/recovery; incomplete collection cannot clear an incident; initial coverage notice explicitly says successful feed refresh is unknown.
- [ ] Test exact thresholds from spec: empty table, family loss, >50% count fall, configured-rule drift, positive outbound UUID counter delta, four complete zero-delta polls for recovery, reset/reload does not recover, eight-sample minimum and >5x median plus >=1,000 new rows for log warning.
- [ ] Test collector failure modes: 401/403, ToolHive envelope error, tool not found, unsupported schema, overflow/timeouts, moving totals, log saturation, missing timezone, unmatched rid, out-of-order/duplicate logs, clock rollback. None yields an all-clear or unqualified exact volume.
- [ ] Test corrupt state, changed baseline schema, concurrent invocation and process failure during atomic replacement. Keep bounded 96-sample history; no unbounded raw logs or secrets. Test tool-response text cannot become an instruction or command.
- [ ] Implement pure analyzer, collector and CLI using the existing runtime adapter after inspecting its current import/call contract. Dependency-inject `call_tool` for tests; no direct HTTP to OPNsense and no subprocess interpolation of returned data.
- [ ] Run `python3 -m unittest discover -s kubernetes/apps/ai/hermes/app/resources -p 'test_opnsense_spamhaus_watch.py' -v` and `python3 -m py_compile kubernetes/apps/ai/hermes/app/resources/opnsense_spamhaus_watch.py`. Assert the healthy fixture writes zero stdout bytes.

Baseline example contains only schema and synthetic rule fixtures. Live approved
UUIDs/attributes are gathered in Task 5, stored on the Hermes PVC with no secrets,
and not silently changed when rules drift. State path is the exact path in the spec.

## Task 4: GitOps integration and enforced boundaries

**Create:**

- `kubernetes/apps/ai/toolhive/mcp/opnsense.yaml` — MCPServer + MCPToolConfig
- `kubernetes/apps/ai/toolhive/mcp/opnsense-networkpolicy.yaml`
- `kubernetes/apps/ai/toolhive/mcp/opnsense-secret.sops.yaml` — authorized SSH-to-SOPS provisioning completed below; keep unregistered

**Modify:** `kubernetes/apps/ai/toolhive/mcp/kustomization.yaml` and
`kubernetes/apps/ai/toolhive/mcp/virtualmcpservers.yaml`.

**Consumes:** Task 2 published digest, spec's exact tools/env/TLS, authorized encrypted
Secret `opnsense-mcp` containing API key/secret. **Produces:** workload `opnsense`
in group `all`, discoverable only as its six read tools through unified aggregation.

- [ ] Read scoped Kubernetes guidance and current ToolHive CRD schemas. Render peer stdio workloads and inspect actual unified/backend labels, proxy ports, probe/scrape identities. Pin selectors to those verified identities; record evidence before writing policy.
- [ ] Add digest-pinned MCPServer: stdio; monitor-only and snake_case configuration; verified HTTPS target; fixed alias/marker; secrets via `spec.secrets`, never literal env values. Set requests 10m CPU/64Mi memory and limits 200m CPU/256Mi memory for backend; proxy resources follow the adjacent stdio pattern. Disable token automount and enforce the spec's pod security.
- [ ] Add six-tool allowlist and unified `aggregation.tools` entry. Test serialized schema/tool names against the image catalog. No wildcard raw API tool and no generic dispatcher.
- [ ] Add backend-scoped deny ingress to defeat the existing intra-namespace allow union, retaining only unified and required verified health identities. Restrict backend egress to firewall TCP/443 plus DNS. Preserve existing unified access; do not accidentally isolate every MCP or proxy/operator.
- [ ] Give the user a non-secret template with Secret name/namespace and the two key names. User creates/encrypts the target with SOPS locally; do not read/decrypt the result. Add it to Kustomization only after encrypted values exist. Run repository staged secret guards without bypassing them.
- [ ] Run `bash .agents/skills/pr-review/scripts/validate-pr.sh`, rendered-selector checks and schema validation. Check no domain literals, plaintext secrets, floating images, missing resources or unmanaged endpoint exposure. Request review; ask before push/apply/reconcile.

Test network isolation after authorized rollout, not by assuming policy intent.
In-process tool registration plus HTTP allowlisting is the first security layer;
network and unified filters are additional layers. If direct-backend isolation or
healthy proxy lifecycle cannot be demonstrated, stop before Task 5 enablement.

## Task 5: Live acceptance and paused-to-enabled handoff

**Modify:** the two planning documents with verification results; no new manifest
scope. **Runtime writes, separately approved:** stage the reviewed script/baseline
on Hermes PVC, create the paused cron job, run verification and resume.

- [ ] Confirm user's effective privileges match the spec and no inherited admin access. Verify TLS from MCP runtime; initialization succeeds without firmware/config-backup privileges. Never print Secret data or credential-bearing requests.
- [ ] Through unified, verify paginated table count/families, configured feed properties, three marked MVC rules and their UUIDs/attributes, runtime-ID mapping and reset-aware counters. Compare against user-confirmed PF baseline. Record unknown freshness and log coverage explicitly.
- [ ] Prove allowed gateway tools work and forbidden tool invocation fails. Denied-call tests against the live backend must stop at MCP dispatch: never attempt a potentially destructive firewall HTTP request to test credentials. Verify non-unified pod cannot reach backend proxy and MCP cannot reach unrelated LAN targets using controlled policy tests.
- [ ] Stage script under `/opt/data/scripts/opnsense_spamhaus_watch.py` as Hermes user, create approved baseline under `/opt/data/cron/state/`, and run read-only collection end-to-end. Use synthetic local alert/recovery fixtures for delivery acceptance, not firewall rule edits or generated malicious traffic.
- [ ] After approval for runtime installation, create the job paused using the verified CLI below; inspect resulting job ID/config. Ensure cron-health monitoring sees it. Avoid creating a duplicate on retry.

```bash
hermes cron create 'every 15m' \
  --name opnsense-spamhaus-watch \
  --script opnsense_spamhaus_watch.py --no-agent --catch-up off \
  --deliver 'discord:1509648802678702203:1531266425594056777' \
  --failure-deliver 'discord:1509648802678702203:1531266425594056777' \
  --paused --paused-reason 'Pending live read-path and delivery acceptance'
```

- [ ] Verify current `hermes cron run/resume/pause --help` before using those commands with the returned job ID. Run while paused only if current CLI supports it; verify `last_status=ok`, durable run output, one accepted alert/recovery delivery, and zero-byte healthy output.
- [ ] Resume only after all gates pass. Verify the first actual scheduled execution and next-run time. Report what is monitored and the daily-refresh-success/log-coverage limitations; only now say monitoring is active.
- [ ] Record rollback: pause returned job ID first; revert approved GitOps changes; revoke dedicated API key if warranted. Do not delete or change Spamhaus firewall policy. No push, deletion or reconcile without approval.

## Planning verification and immediate handoff

Planning changed only these two Markdown files. Validate with `git diff --check`,
Markdown lint on the two files, and local-link checks. Do not claim implementation
tests passed or a working monitor from documentation checks.

The credential setup described in spec Manual Setup steps 1–3 was subsequently
authorized and completed by SSH-to-SOPS provisioning below; do not recreate the
account or key. The next agent must preserve the source pin, inspect staged scope,
and request publication and live deployment permissions at their respective gates.

## Authorized credential provisioning evidence — 2026-11-07

- On OPNsense 26.7.8_2, created `hermes-spamhaus-monitor` with native UID 2004
  under the configuration lock, native validation/serialization/save, collision
  checks and a random unknown password hash. Native `auth user changed` notification
  was used; broad OS-user synchronization was not run.
- Persisted model verification confirmed exactly the seven approved direct and
  effective privileges, empty groups/shell/authorized keys, no `page-all`, and one
  API key via `count($user->apikeys->all())`. An initial legacy XML-container count
  was inapplicable to this version's string storage; no regeneration occurred.
- Streamed only the new pair from its remote root-owned 0600 artifact into
  `kubernetes/apps/ai/toolhive/mcp/opnsense-secret.sops.yaml`, Secret
  `ai/opnsense-mcp`, keys `OPNSENSE_API_KEY` and `OPNSENSE_API_SECRET`.
  No plaintext local file, credential output, private identity or decryption used.
- Repository creation rules selected the public age recipient (SHA-256
  `4648528216d72d27df00f02ae0acf77dd01ea24f218482b6ff784ec9f4316556`).
  SOPS 3.13.3 encryption-only preflight and structural identity/key/recipient/MAC
  checks passed. The first relay failed on csh history expansion before transfer;
  stdin-fed PHP retried the same pair successfully, without duplicate provisioning.
- Remote credential-authenticated read smoke passed all seven routes with HTTP
  200, forcing `192.168.0.1` while preserving `opnsense.tanguille.site` TLS checks:
  alias search (1), bounded alias-table read (total 1853), table size (1853),
  rule search (38), counter identities (38), running identities (36), bounded log
  read (1). Only statuses/counts were emitted; this is not MCP/gateway acceptance.
- After encryption and verification, removed only
  `/var/run/opnsense-hermes-spamhaus-mcp.credentials.json`, checking regular-file
  type, root ownership and 0600 mode first; verified absence afterward.
- Only the encrypted Secret is staged; `mise exec -- python3
  scripts/secret-check.py --staged` checked 1 index file with 0 failures.
  Secret remains absent from Kustomization. No push, publication, deployment,
  reconciliation, DNS change or cron enablement occurred. Runtime DNS selection,
  gateway/network acceptance and delivery remain later approval gates.
- Rollback, if separately authorized: revoke the dedicated key/account and remove
  the unused ciphertext; never modify Spamhaus firewall policy for rollback.

## Approved feature publication preflight — 2026-11-08

- User explicitly authorized commit/push of this feature branch and GHCR image
  publication, not merge, deployment, DNS changes or Hermes enablement.
- Branch `docs/opnsense-spamhaus-mcp-plan` had no upstream; fetched origin without
  merging unreviewed main changes. Exact staged scope: 21 intended files across
  Docker MCP, manual workflow, Hermes collector/tests/example, spec/plan and the
  encrypted Secret. No bytecode/cache files staged. All 21 passed the staged
  secret guard and whitespace check.
- Fresh publication checks: actionlint 1.7.12 passed; 40 Hermes tests passed;
  Docker test target passed 767 upstream/extension tests; runtime build, UID 65532
  and restricted stdio smoke passed (expected rejected-bound/unavailable calls).
  Verified upstream archive SHA-256 and base index's recorded amd64 child against
  upstream/registry, without changing pins. Parent full-repository validation
  evidence is reused, not rerun.
- GitHub reports HTTP 404 for this new workflow on the default branch. Do not
  merge just to dispatch it. Authorized equivalent local buildx publication will
  use the committed source, same amd64 runtime target, full revision label,
  unique revision tag, maximum provenance and SBOM, through existing Docker GHCR
  authentication. Registry results will be recorded after publication.
- Additional review delegation was unavailable due to session depth limit;
  publication scope/protections were inspected directly. No new independent
  review is claimed.

### Publication attempt outcome — blocked on GHCR authentication

- Implementation commit `ffaa397c1e9281b987bde62940cebc4f2f04da45` was pushed
  normally to `origin/docs/opnsense-spamhaus-mcp-plan`; no PR/merge was created.
  Commit hooks passed all-file secret guard and workflow zizmor checks.
- Repeated actionlint, 40 Hermes tests, Docker test/runtime builds and restricted
  stdio smoke on the clean committed source after hook formatting. All passed.
  Actual branch workflow dispatch returned HTTP 404 (workflow not on default).
- Equivalent local buildx used `linux/amd64`, runtime target, committed revision
  label, `--provenance=mode=max --sbom=true`, and tag
  `ghcr.io/tanguille/opnsense-mcp:0.4.2-monitor.ffaa397c1e92`.
  Build and SBOM scan completed, but GHCR rejected layer push:
  `permission_denied: The token provided does not match expected scopes.`
  Existing GitHub CLI auth has `gist, read:org, repo, workflow`, without
  `write:packages`; no token replacement or permission changes were attempted.
- No successfully published registry digest is established. Locally generated
  export digests are not deployment references. Package metadata query returned
  404; registry platforms, attestation retrieval, visibility and anonymous pull
  cannot be certified. Do not register or deploy this image.
- Next prerequisite: user provisions suitable GHCR write authentication through
  their normal secure channel (never chat), then retry this exact reviewed source
  tag after checking for any existing registry manifest. Verify registry digest,
  amd64 platform, revision/source labels, provenance/SBOM, visibility and anonymous
  pulling before declaring publication complete. No deployment is authorized.
