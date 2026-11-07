# Restricted OPNsense MCP (local preparation)

Derived from lucamarien/opnsense-mcp-server 0.4.2 at the exact commit in
`upstream.lock.json`. The original MIT license, client, lifespan, package version,
and stdio transport are retained. Publication evidence is recorded in the plan;
no workload has been deployed.

## Build and test

From the repository root (Docker required):

```bash
docker build --platform linux/amd64 --target test -t opnsense-mcp:test docker/opnsense-mcp
docker build --platform linux/amd64 --target runtime \
  --build-arg REVISION="$(git rev-parse HEAD)" -t opnsense-mcp:local docker/opnsense-mcp
python3 -m venv /home/tanguille/.cache/opencode-tmp/opencode/opnsense-test
/home/tanguille/.cache/opencode-tmp/opencode/opnsense-test/bin/pip install \
  --require-hashes -r docker/opnsense-mcp/test-requirements.lock
/home/tanguille/.cache/opencode-tmp/opencode/opnsense-test/bin/python \
  docker/opnsense-mcp/tests/container_smoke.py opnsense-mcp:local
```

The `test` target runs `python -m pytest -q` against both upstream tests and
the extension tests. The smoke test uses synthetic credentials, disabled
networking, UID 65532, read-only root, a bounded temporary filesystem, dropped
capabilities, and no privilege escalation. It checks stdio initialization, the
exact six-tool catalog, rejected unknown tools, rejected bounds, and sanitized
unavailable reads. It does not contact OPNsense.

The runtime contains no git/compiler and inherits system certificate trust.
A root-owned package `MONITOR_ONLY` marker forces restricted registration and
configuration even when `OPNSENSE_MONITOR_ONLY=false` overrides the environment.
It also rejects constructing an unrestricted client inside the runtime image.
The marker is present only in the restricted runtime stage; upstream regression
tests can still exercise the optional-profile source. Container smoke explicitly
sets the false override and verifies the six described read tools remain the
entire catalog. `OPNSENSE_API_STYLE=snake_case` is required.
Deployment must set the verified HTTPS `/api` URL and provide a new
dedicated API key/secret through the workload Secret. Neither writes, sensitive
mode, nor TLS verification disablement is accepted in monitor mode. Generic
client request methods are disabled; only the seven fixed read endpoints used
by the six tools can be reached. Firmware detection is bypassed.

Build and publication architecture is **linux/amd64 only**. Confirm final node
scheduling before deployment. No cross-platform runtime claim is made.

## Source and dependency maintenance

`prepare.py` downloads the pinned archive, verifies SHA-256, safely extracts it,
then uses `git apply --check` and `git apply` (no fuzz) to apply the patch. Tests
must pass before rebasing. Never silently update the source pin or hashes to
make a failing patch apply. Revalidate the OPNsense route/response contracts on
every target release update. Python base image is pinned by OCI index digest;
the exact amd64 child manifest is recorded in the lock.

`requirements.lock` and `test-requirements.lock` were generated with Python 3.13
and `pip-compile --generate-hashes --strip-extras` from the corresponding `.in`
files. Installation uses `--require-hashes`; upstream installation disables
dependency resolution and isolated build downloads. Regenerate both together,
review version/hash changes, then rebuild both targets and run smoke tests.

The manual GitHub Actions workflow defaults to test-only. After separate push
and publication approval, `publish=true` publishes
`ghcr.io/tanguille/opnsense-mcp:0.4.2-monitor.<12-character-cluster-sha>` with
OCI source/revision labels, SBOM and maximum build provenance. Save the resulting
manifest digest, verify package visibility and credential-free pulling, and
inspect attestations before referencing it in GitOps. Local image digests are
not GHCR deployment references. An uncommitted local tree is not proven by its
HEAD revision label; reviewed committed source is required for publication.

## Version-specific evidence

OPNsense 26.7.8's annotated tag object is `08dc30b62227af4e488c93f63af1344a79343b8d`;
its actual commit is `96978001a8343669154e4492172ce0a07dac7e4b`.
The source checks used `AliasController`, `AliasUtilController`, `FilterController`,
`FilterUtilController`, diagnostics `FirewallController`, `ApiControllerBase`,
and filter scripts `pftablecount.py`, `rule_stats.py`, `list_table.py`,
`list_rule_ids.py`, `read_log.py` at that commit.

- `rowCount` is the number **returned**, not the requested page size; the final
  partial page and empty pages preserve it. The collector rechecks totals and
  first-page identities; this does not make PF table reads atomic.
- Rules pages retain all MVC rows/fields so pagination can be proved before the
  collector locally selects `SDROP-20261106-v1`. Statistics remain UUID/hash keyed;
  descriptions are never used to invent joins.
- `FilterController::searchRuleAction` also merges `list_non_mvc_rules.php`
  output. Legacy `uuid` is copied from a potentially repeated/empty label;
  legacy rows instead have a unique priority/sequence `sort_order`. Pagination
  validates those keys and compares the complete first page again. MVC identities
  remain strict and duplicate/legacy marked identities are rejected.
- `table.count` is a count hint, not the live count. Cache `updated` is content
  mtime, never refresh success. `refresh_success` always remains `unknown`.
- Log rows preserve upstream fields; a digest comes only from upstream rows.
  Naive/malformed timestamps yield unknown coverage, never an exact rate.

## Hermes adapter and enablement gates

The local Hermes analyzer/collector is prepared and tested, **not installed**.
The installed `/opt/data/.hermes/scripts/toolhive_retry.py` was inspected read-only
in `ai/deploy/hermes`, container `app`. ToolHive discovery offered no pod-exec/read
tool, so inspection used `kubectl exec` with `python3 -B` (no bytecode writes).
No credential environment/config values were read or printed.

`runtime_adapter()` lazily imports that fixed module and uses its async
`call_toolhive("find_tool", ..., max_retries=1, backoff=0)` contract. Each of the
six exact expected gateway names (`opnsense_opn_monitor_*`) must appear exactly
once in discovered descriptors before invoking it; missing, duplicate, suffix or
substring matches fail closed. Discovery happens inside the 120-second snapshot
deadline and bindings are cached for that snapshot. Calls use the installed
`_raw_call_tool(exact_name, arguments)` helper, which invokes gateway `call_tool`
and checks the outer MCP `isError` flag. The SDK helper discards that flag on
backend calls, so it is not used for those. Nested envelopes/schema/size bounds
are validated locally; helper diagnostic output is discarded, never delivered.
Read-only AST reinspection confirmed positional signatures:
`call_toolhive(method, params=None, max_retries=3, backoff=5)` and
`_raw_call_tool(tool_name, params)`. The latter receives the exact discovered name
first and the argument dictionary second. Inspected source SHA-256:
`d99ee24bc6860af0a5e6edb9bf0dcc64211a1abd28c3a7a4eb765867576173cc`.

Actual read-only transport probes did **not** succeed: the SDK discovery helper
returned `(False, None, 1, ...)` after a generic TaskGroup failure; its raw helper
raised `RuntimeError` for a previously discovered read-only Kubernetes metrics
tool. Error bodies were not printed. This is recorded as an actual live-acceptance
gate, not an inability to inspect the installed adapter. No monitor backend is
deployed yet; successful six-tool live discovery/calls still require Task 5.

Important target limitation: 26.7.8 `read_log.py` strips positive timezone offsets
from RFC5424 timestamps and emits naive RFC3164 timestamps. Current code correctly
reports those as unknown. No timezone is inferred. Per-signal recovery is
independent: complete table/rule evidence can recover table/rule incidents;
four complete zero-delta counter polls recover outbound incidents even with
unknown log times. Counter resets or missing counter evidence break that streak.
Log incidents/history recover/update only with their own complete timestamp,
identity, order and digest-continuity evidence. Thus log uncertainty does not
disable independent health monitoring or imply an exact log rate. Cache mtime
still never proves successful retrieval. No runtime timezone workaround was added.

Individual signal collection failures no longer abort independent reads. Logs
depend on validated running IDs, not successful configured-rule/counter parsing;
one global 120-second deadline still stops all collection. Log gaps retain the
confirmed head and the coverage incident. A valid unsaturated window may set a
separate pending anchor, but never contributes a complete rate sample. A later
complete overlapping window can establish a new baseline; recovery explicitly
says the previous gap remains unknown. Invalid/saturated windows cancel that
pending interval. This avoids both silently dropping gaps and permanent recovery
lockout. Production-path volume tests use actual paginated collection and log
samples/digests, not only injected counts.

Baseline example is synthetic and `approved: false`; replace it only with a
human-approved read-path baseline on the Hermes PVC later. Full configured rule
objects must be captured, not the abbreviated example. State writes are atomic,
locked, bounded and contain no raw traffic rows. Changed approved baseline is a
hard error requiring deliberate state migration.

An explicitly authorized dedicated account and encrypted, unregistered Secret
are prepared. No HelmRelease, Kustomization registration, network policy, cron job
or runtime file was changed. Later cron settings remain `every 15m`, `--no-agent`,
`--catch-up off`, initially paused, with alert/failure delivery to
`discord:1509648802678702203:1531266425594056777` and cron-health coverage.
Rollback after eventual deployment pauses that job first, then reverts monitor
GitOps changes with approval. Never roll back the firewall's Spamhaus rules.
