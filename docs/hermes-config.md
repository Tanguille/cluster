# Hermes runtime config

Hermes' behaviour is driven by `/opt/data/config.yaml` **on the PVC, not in git**. Flux
only manages the HelmRelease (`kubernetes/apps/ai/hermes/app/helmrelease.yaml`); nothing
in this repo renders the file below. Editing it is a live change to a stateful pod.

```sh
POD=$(kubectl get pod -n ai -l app.kubernetes.io/name=hermes -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n ai $POD -- grep -n -A6 '^discord:' /opt/data/config.yaml
```

Every change needs `kubectl rollout restart deploy/hermes -n ai`. The YAML→env bridge runs
once at gateway start; there is no live reload.

## Two `discord:` blocks — only the top-level one is read

`config.yaml` carries both a top-level `discord:` block and a `platforms.discord:` block.
`gateway/config.py:1533` reads the top-level one and only falls back to `platforms.*`
when the top-level key is **absent**:

```python
platform_cfg = yaml_cfg.get(entry.name)        # top-level discord: — wins
if not isinstance(platform_cfg, dict):          # only if absent
    for _src in (gateway_platforms, yaml_cfg.get("platforms")):
```

Editing `platforms.discord.*` while a top-level `discord:` exists is a **silent no-op** —
no warning, no log line, the value simply never reaches the adapter. This cost a full
debugging session on 2026-07-30. Same shape applies to `slack:`, which also has both.

`platforms.homeassistant.extra:` has no top-level twin and *is* the live path.

## `hermes config set` mangles lists

`hermes config set <key> a,b` writes a comma-joined **string**, not a YAML list. For any
list-valued key (`watch_entities`, …) edit the file directly and re-parse to confirm:

```sh
kubectl exec -n ai $POD -- python3 -c \
  'import yaml; print(yaml.safe_load(open("/opt/data/config.yaml"))["platforms"]["homeassistant"]["extra"])'
```

Digit-only values are also coerced to int; pass a trailing-comma string to keep them text.

## Discord: mention gating

Messages in a guild need an `@mention` unless one of these holds
(`plugins/platforms/discord/adapter.py:7154`):

- the channel matches `free_response_channels`, or
- the thread is in `/opt/data/discord_threads.json` (threads hermes auto-created).

Cron output threads are created by the scheduler and **never** call `_threads.mark()`, so
they stay untracked and silently swallow mention-free replies. No log line is emitted on
the drop. Worked around by listing the cron thread ids in `free_response_channels`:

```sh
kubectl exec -n ai $POD -- sh -c 'T=$(grep ^DISCORD_BOT_TOKEN /opt/data/.env | cut -d= -f2- | tr -d "\"");
  curl -s -H "Authorization: Bot $T" \
    "https://discord.com/api/v10/guilds/934782022549901322/threads/active" |
  python3 -c "import json,sys; print(\",\".join(t[\"id\"] for t in json.load(sys.stdin)[\"threads\"] if t[\"name\"].startswith(\"cron:\")))"'
```

**This list needs updating whenever a cron job is added or its thread is recreated.**
Upstream fix would be `_threads.mark(thread_id)` on cron delivery; no issue filed yet
(#66172 and #73478 are adjacent but both assume the message already reached the agent).

Do **not** put the parent channel id (`#hermes`) in `free_response_channels`: `is_free_channel`
also sets `skip_thread` (adapter.py:7166), which disables auto-threading for the whole
channel. Scope to thread ids only. Upstream #26058 tracks this coupling.

## Home Assistant watch entities

`platforms.homeassistant.extra.watch_entities` fires a **full agent turn per state change**
— roughly a 74K-token prompt each. Continuously-varying analog sensors (power meters, live
temperatures) must never appear here; `sensor.p1_meter_power` alone produced 762 turns in
19h. `cooldown_seconds` did not suppress them.

Pick entities by measured churn, not by name. 48h change counts via the recorder:

```sh
curl -s -H "Authorization: Bearer $HASS_TOKEN" \
  "$HASS_URL/api/history/period/$START?filter_entity_id=$IDS&minimal_response&no_attributes"
```

Current list is discrete-state and near-zero churn: solar inverter status, zigbee bridge
connectivity, ems-esp system status, boiler heat demand.

## Compression threshold is derived from `model.context_length`

`context_compressor.py:1266`:

```python
effective_window = context_length - (max_tokens or 0)   # max_tokens unset -> full window
threshold_tokens = max(int(effective_window * threshold), MINIMUM_CONTEXT_LENGTH)  # min = 64_000
```

So `model.context_length` **must match what SGLang actually serves**
(`kubernetes/apps/ai/llmkube/models/qwen36-27b-sglang.yaml:179`, currently `contextLength: 180000`).
It was set to 200000 while SGLang served 180000, which made every `X/200K` figure in cron
footers wrong and would have overshot the KV pool had the threshold been raised naively.

Size the threshold from measured session usage, not by feel:

```sh
kubectl exec -n ai $POD -- python3 -c 'import json;d=json.load(open("/opt/data/sessions/sessions.json"));
v=sorted(x["last_prompt_tokens"] for x in d.values() if isinstance(x,dict) and x.get("last_prompt_tokens"));
print([ (p, v[min(len(v)-1,len(v)*p//100)]) for p in (50,75,90,95,99,100) ])'
```

Measured 2026-07-31 over 61 sessions: p50 51K, p90 94K, p95 106K, p100 112K. Threshold 0.7 x
180K = 126K sits above the observed maximum (so routine work never compresses) and 54K below
the pool ceiling (so no long-context thrash). Compression remains as an outlier safety net.

Do not raise this blindly: threshold 0.5 was originally a deliberate fix for
`Compression aborted: timed out`, caused by the summary prefill landing on a saturated GPU.
A higher threshold means a larger summary prefill when it does fire. The mitigations that
make 0.7 safe are `auxiliary.compression.model: qwen-3.6-fast`, `timeout: 600`, and
`abort_on_summary_failure: false` — check all three are still set before touching it.

`compression:` (top level, policy) and `auxiliary.compression:` (summarizer model/timeout)
are different blocks, not duplicates.

## state.db size is bounded, not leaking

1.33 GB, of which ~1.05 GB is FTS index over 283 MB of actual messages — the trigram index
alone is 710 MB, and `messages_fts_content` + `messages_fts_trigram_content` are 236 MB each
of duplicated message text (non-external-content FTS). That ratio is structural; shrinking it
is an upstream schema change.

Growth *is* capped: `sessions.retention_days: 90` with `auto_prune: true` and
`vacuum_after_prune: true`, and there are zero sessions older than 90 days — auto-prune is
working. The only lever here is retention, which is a data decision, not a tuning one:

| bucket | sessions | messages |
|--------|---------:|---------:|
| <7d    | 130      | 20124    |
| 7-30d  | 612      | 22047    |
| 30-90d | 467      | 28067    |

Dropping `retention_days` to 30 would remove ~40% of messages. Upstream #54189 describes this
as unbounded growth; that does not match this instance.

## Skills: gate on measured usage, not on the name

`skills.disabled` keys on the skill's **registered name**, which is not always the directory
basename (`skills/mlops/audiocraft` registers as `audiocraft-audio-generation`). Always
confirm against `hermes skills list`, never against `find … -name SKILL.md` — a directory walk
also picks up `.archive/`, which hermes does not register at all.

Real usage comes from the `skill_view` tool calls, not from grepping transcripts (the skill
catalogue is injected into prompts, so every skill "appears" hundreds of times):

```sh
kubectl exec -n ai $POD -- python3 -c '
import sqlite3,json,collections
c=sqlite3.connect("file:/opt/data/state.db?mode=ro",uri=True); cnt=collections.Counter()
for (tc,) in c.execute("select tool_calls from messages where tool_calls like \"%skill_view%\""):
    for call in json.loads(tc):
        fn=call.get("function") or {}
        if fn.get("name")=="skill_view":
            n=json.loads(fn.get("arguments") or "{}").get("name")
            if n: cnt[n]+=1
print(cnt.most_common())'
```

Disabling a skill only removes it from the catalogue; the files stay on the PVC, so it is
reversible by deleting the entry.

Size is **not** worth optimising. `prompt_builder.py:1587` injects only `(name, description)`
per enabled skill and truncates each description to 60 chars — the whole catalogue is ~512
tokens at 33 enabled skills. A SKILL.md body only costs context when `skill_view` loads it.

### Stale infra references are the real skill defect

A skill that names decommissioned infrastructure does not fail loudly — it sends the agent
down a dead path and then reports the resulting silence as a finding. Audit enabled skills
against live cluster state, not against their own prose. Two live examples found 2026-07-31:

| Skill | Stale claim | Reality |
|---|---|---|
| `prometheus-cluster-health` (6h cron) | VolSync/Kopia triage; "VolSync metrics + fallback unavailable → mark degraded" | VolSync decommissioned 2026-07-12. The metrics are permanently gone, so the rule fired structurally every run. Replaced with kopiur metrics from `PrometheusRule kopiur-system/kopiur-controller`. |
| `homelab-gitops-digest` (daily cron) | "**Always** call `github-opt_list_commits`", plus a 3x30s retry loop | No `github-opt` MCPServer exists (removed for toolhive#5890). Every run burned 90s on retries before falling back. GitHub REST over `terminal` + `curl` is now the primary path. |

The check that finds these:

```sh
kubectl get mcpserver -A                       # what backends actually exist
kubectl exec -n ai $POD -- sh -c 'cd /opt/data/skills; grep -rl "<dead-thing>" --include=SKILL.md . | grep -v .archive'
```

`hermes cron list` shows which skills each cron loads — those are the ones where a stale
reference costs something every day.

### Zero usage can mean "unreachable", not "useless"

`skill_view` count alone is a bad disable signal. A skill nothing links to gets zero loads
no matter how good it is. Check whether any *enabled* skill actually routes to it before
concluding it is dead:

```sh
# for each enabled skill body, grep for the names of disabled skills
kubectl exec -n ai $POD -- python3 - <<'PY'
import json, yaml, os, re, collections
snap = json.load(open("/opt/data/.skills_prompt_snapshot.json"))
dis = set((yaml.safe_load(open("/opt/data/config.yaml"))["skills"] or {}).get("disabled") or [])
names = {e["frontmatter_name"]: e["skill_name"] for e in snap["skills"]}
hits = collections.Counter()
for n, path in names.items():
    if n in dis: continue
    f = f"/opt/data/skills/{path}/SKILL.md"
    if not os.path.exists(f): continue
    body = open(f, errors="ignore").read()
    for d in (x for x in names if x in dis):
        if re.search(rf"\b{re.escape(d)}\b", body): hits[d] += 1
print(hits.most_common())
PY
```

`dynamic-workflow` (fan-out orchestration + adversarial-convergence verification) had 0
loads purely because nothing pointed at it. Fixed by linking it from `council`,
`homelab-infra-kanban`, and `research-discovery-and-knowledge` rather than deleting it.

Two traps when re-enabling:

- **Conditional skills are not disabled.** `kanban-orchestrator` carries
  `environments: [kanban]`, so `_skill_should_show` hides it from the default catalogue.
  It is absent from `hermes skills list` output but has 38 real loads. Do not "fix" it.
- **Skills can reference skills that were never installed.** `dynamic-workflow` pointed at
  `delegate-task-output-patterns` and `kanban-multiagent`; neither exists on this PVC.
  Dead pointers send the agent looking for a skill it cannot load.

## Kanban workers: two independent bugs that killed the board

Measured 2026-07-31. The board looked idle (0 `ready`, 0 `todo`, 33 `blocked`); it was
actually failing at 99.7%.

| Month | done | crashed |
|---|---:|---:|
| 2026-05 | 22 | 3 |
| 2026-06 | 10 | 225 |
| 2026-07 | **2** | **717** |

All activity stopped 2026-07-29 11:35 because every card had drained into `blocked`.
32 of the 33 blocked cards sat at `consecutive_failures = 2` — the `--failure-limit`
default — so they were **not** waiting on Tanguille, they were dead workers.

### Bug A — profile configs had no `model:` block

`kanban_db.py:8209` sets `env["HERMES_HOME"] = resolve_profile_env(profile)`, so the worker
subprocess reads `/opt/data/profiles/<name>/config.yaml`, **not** the root config. Those
files carried only `agent:`, `toolsets:`, `approvals:`, `display:`, `kanban:`, `skills:`.
No provider → the worker prints the first-run setup wizard banner and exits 1.

Reproduce in one line — this is the whole bug:

```sh
kubectl exec -n ai $POD -- /command/s6-setuidgid hermes \
  env HERMES_HOME=/opt/data/profiles/kubernetes-operator \
  /opt/hermes/bin/hermes config get model.provider     # -> "Config key not set", rc=1
```

Fixed by copying the root `model:` block into all six profile configs. This duplicates the
gateway key six times; there is no inheritance from the root config (verified — a missing
key is simply missing, not inherited). Re-check after any credential rotation.

### Bug B — worker profiles blocked the `kanban-worker` skill

Profile configs are an **allowlist**: the `# allow:` comment is the intent and
`skills.disabled` is its generated complement over the globally-enabled set. Add to the
allow list, never delete from `disabled` alone.

140 tasks carry `skills: kanban-worker` in their task record, and every assignee profile
had `kanban-worker` in its `disabled` list — kubernetes-operator (74 tasks),
homelab-researcher (40), homeassistant-operator (22). Only `homelab-orchestrator` allowed
it. That is the 236 runs recorded as:

```text
worker exited cleanly (rc=0) without calling kanban_complete or kanban_block — protocol violation
```

The worker ran but never loaded the skill that defines the completion contract. Several
profiles also blocked `council` while `homelab-infra-kanban` mandates the council gate —
a skill instructing the agent to load something its own profile forbids.

### Checks worth re-running

```sh
# every profile resolves a provider
for p in /opt/data/profiles/*/; do env HERMES_HOME=$p hermes config get model.provider; done

# no profile blocks a skill its tasks request
kubectl exec -n ai $POD -- python3 -c "
import yaml,pathlib
for p in sorted(pathlib.Path('/opt/data/profiles').glob('*/config.yaml')):
    d=set((yaml.safe_load(p.read_text()).get('skills') or {}).get('disabled') or [])
    print(p.parent.name, sorted({'kanban-worker','council'} & d) or 'ok')"
```

Also: `assignee = opnsense-network-operator` exists on 2 tasks with **no matching profile
directory**. `resolve_profile_env` raises `FileNotFoundError` for those, so they can never
dispatch. Create the profile or reassign.

`hermes kanban show <id>` crashes with `TypeError: 'str' object cannot be interpreted as an
integer` (`kanban.py:50`, `created_at` is a string). Cosmetic, but it aborts the command
after printing status — read the DB directly when it bites.

## OpenCode as the Kanban implementation lane

Hermes owns the card, OpenCode owns the edit. Hermes has **no GitHub token** (its `.env`
has 22 keys, none GitHub) so it cannot open a PR; OpenCode's VM is authenticated as
`Tanguille` with `gh` and does it directly.

**There are two OpenCode instances and only one works:**

| | pod `opencode` | VM `opencode-vm` (port 4096) |
|---|---|---|
| `/api/location` | `/home/tanguille/cluster` | `/home/tanguille/cluster` |
| does it exist | **no** — pod has no `git`, no clone | yes, with `.git`, `.worktrees/`, `gh` auth |

`opencode-vm` is an EndpointSlice pointing at an address outside the cluster, which is why
the in-cluster service resolves but changes nothing. Both `/api/location` values look
identical — the only reliable check is whether the directory actually exists.

Transport is `/opt/data/scripts/opencode_lane.py` (`run` / `submit` / `wait` / `health`,
`OPENCODE_URL` and `OPENCODE_TIMEOUT` overridable). The REST contract is
`POST /api/session` → `POST /api/session/{id}/prompt` with `{"prompt":{"text":...}}` →
`GET /api/session/{id}/message`, reading `content[].text` from the newest `type:
"assistant"` entry. A message still streaming has **no `finish` key** — absence means
in-flight, not done; polling that treats missing-as-done returns empty output.

Brief construction and reconciliation live in the `opencode-kanban-lane` skill. The
load-bearing rule: build the brief from the card's own fields (title, evidence, named
paths), never from recollection, and re-run the verification command yourself before
completing the card.

### Editing skills from outside the pod

`kubectl cp` writes as uid 1000 mode 644, but hermes runs as **uid 10000**. Always fix up
afterwards or the agent cannot rewrite its own skills:

```sh
kubectl exec -n ai $POD -- sh -c 'chown 10000:10000 <path>/SKILL.md && chmod 664 <path>/SKILL.md'
```

## Security posture — known-weak defaults

Verified 2026-07-31; re-check these before assuming any of them protect anything.

| Setting | Value | Consequence |
|---|---|---|
| `security.tirith_fail_open` | `true` | tirith (pre-exec scanner for `terminal`, the busiest tool) **is** installed and healthy — 0.3.1 at `$HERMES_HOME/bin/tirith`, threat DB current. Fail-open only matters when the scanner itself errors. `command -v tirith` looks missing unless `$HERMES_HOME/bin` is on PATH; check `/opt/data/bin/tirith --version` instead. |
| `security.allow_lazy_installs` | `true` | agent may install arbitrary packages at runtime |
| `skills.inline_shell` | `true` | skill documents can execute shell |
| `memory.write_approval` | `false` | memory writes are unreviewed (`skills.write_approval` *is* `true`) |
| `fallback_providers` | `[]` | gateway/SGLang is a hard single point of failure |

Auxiliary entries with `provider: main` inherit the main block's `base_url` and `api_key`
(`auxiliary_client.py:314`), so they must never carry their own copy. `auxiliary.vision` did,
duplicating the gateway key; it is now `provider: main` and the key appears exactly once in
the file. Keep it that way — `grep -c` the key after any edit.

### Pin MCP server versions

`memory_graph` and `filesystem` run `npx -y @modelcontextprotocol/server-…` with no version,
resolving latest on every gateway start. That is the exact failure mode that killed `time` and
`fetch` (see below): an upstream release changed under a floating resolver and the server
died at import. Pin them (`@x.y.z`) so a restart cannot change behaviour.

## MCP `time` / `fetch` pinned to `mcp<2`

Both servers still import `McpError`, renamed to `MCPError` in mcp SDK 2.0, so `uvx`
resolving the latest SDK kills them at import. Hermes reports this as a generic
`Connection closed`, identical to the unrelated (and fixed) hermes #66423.

```yaml
mcp_servers:
  time:
    args: [--from, mcp-server-time, --with, mcp<2, mcp-server-time]
```

Drop the pin once modelcontextprotocol/servers#4560 (fetch) and #4570 (time) ship.
Verify with `hermes mcp test time`.
