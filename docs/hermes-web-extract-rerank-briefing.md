# Hermes `web_extract` rerank plugin: briefing and test findings

Code, configuration and rollout steps: `kubernetes/apps/ai/hermes/plugins/web-extract-rerank/` (README). This
file keeps the facts the plugin rests on and what live testing changed. Checked on 2026-10-17 against
Hermes `v0.22.2` (tag `7b77e29e`).

## Goal

When `web_extract` truncates a page, show the parts that are relevant to the task instead of a fixed head
and tail. The plugin keeps the same character budget and Hermes's footer, so the model can still page
through the stored full text.

## Why (measured)

`state.db`, last 30 days, all profiles:

| Metric | Value |
| --- | --- |
| `web_extract` calls / pages | 1,287 / ~2,063 |
| Truncated pages | 319 (~15%): `main` 163, `homelab-researcher` 75, `kubernetes-operator` 72, others 9 |
| Truncated page size (clean chars) | p50 23,149, p90 87,338, max 1,284,061 |
| Follow-up `read_file` on `cache/web/*.md` | 141 reads on 34 files: only ~11% of truncated pages are ever paged |
| `web_search` calls | 1,225 |

The model often passes its own `char_limit` (a value of 4,000 was seen). No profile sets
`web.extract_char_limit`, so the default is 15,000.

## Hermes facts the plugin depends on

- **Output format.** `web_extract` returns `{"results": [...]}`, serialized as
  `json.dumps(..., indent=2, ensure_ascii=False)` (`tools/web_tools.py`).
- **Truncation.** A page over `char_limit` becomes a 75% head and a 25% tail plus a footer
  (`tools/web_tools_truncate.py`). The footer reads:
  `──────── [TRUNCATED] ────────`, then `Showing ... of N total clean characters.`, then
  `Full text saved to: <path>`, then `To read the omitted middle: read_file path="<path>" offset=<line> ...`.
- **Stored copy.** The full page is stored, capped at 2,000,000 chars, under
  `$HERMES_HOME/cache/web/` for the root profile and `$HERMES_HOME/profiles/<name>/cache/web/` for every
  other profile.
- **Hook order.** `transform_tool_result(tool_name, args, result, task_id, ...)` runs after
  `post_tool_call` and before the result is appended; the first `str` returned replaces the result.
  - It is not timeout-bounded, so the plugin bounds itself.
  - `args` holds only `urls` and `char_limit`.
- **Security envelope.** `state.db` stores tool results wrapped in `<untrusted_tool_result>`. The wrapping
  happens later (`make_tool_result_message`), so the hook sees raw JSON.
- **Enabling and loading.** Plugins load only when listed in `plugins.enabled` of the profile's
  `config.yaml`, and Hermes must restart to load them. Python 3.14, stdlib only (no PyYAML in the venv).

## Reranker contract

`bge-reranker-v2-m3` runs on `llama-server` (Vulkan, iGPU, control-2):

- The endpoint is `POST :8080/v1/rerank`, with body `{model, query, documents, top_n}`.
- The reply is `{"results": [{index, relevance_score}]}`, raw logits, unsorted.
- One slot (`parallelSlots: 1`), and each (query, passage) pair must fit 2,048 tokens or the server returns
  HTTP 500.
- memini is the other client. It sends one request at a time with a 5 s timeout.

The reranker favours short lines that repeat the query's product or version. For a Talos query it scored
"Welcome to the v1.14.1 release of Talos!" at 0.58, against -1.8 for the relevant bind-mount paragraph.

## What live testing changed

Testing ran in the Hermes pod three ways:

- through Hermes's own loader and `invoke_hook`, from an isolated `HERMES_HOME`;
- on 16 real truncated results from `state.db`;
- against the live reranker.

| First design | Final | Why |
| --- | --- | --- |
| Query = user message + `web_search` query, 1,500 chars | Last `web_search` query of the task only, 400 chars | User messages were kanban stubs, "continue now" nudges or 14-20K-char skill preambles. The query repeats in every pair, and at 1,500 chars half the reranks timed out at 6 s. |
| Head = min(25%, 2,500 chars) | Passages at most 60% of the room, the head fills the rest; skip below 75% of the original | Small passages shrank a 30K-char window to 6K. |
| Chunk on every blank line | Pack paragraphs up to 900 chars; a short heading leads the next paragraph | One-line chunks let "Welcome to ..." lines win. |
| Exclude head chunks by line | By char offset | Minified pages are a few huge lines. |
| Keep Hermes's `read_file` hint | Recompute its `offset=` for the new head | The old offset pointed past lines the model never saw. |
| (none) | Skip JSON pages | API JSON loses structure in fragments and is token-dense. |

Final run, 16 real cases:

- 6 rewritten, reranks 1.3-3.0 s, no timeouts;
- 2 skipped as JSON, 1 with no keyword match;
- 7 with no `web_search` in the task, left unchanged.

On the Talos changelog, a query about kubelet `extraMounts` and bind mounts now returns three passages: the
new multi-document config, native BGP, and the workload mount-namespace change.

## Reachability

`ai-ingress-allowlist` (selector `{}`) admits every pod in `ai`, `default`, `kritika`, `observability` and
more to all `ai` pods. Cilium policies union, so the gateway-only inference policy does not restrict
anything: Hermes, and every pod in those namespaces, already reaches the reranker and vLLM. The Hermes entry
in `inference-rerank-memini-allow` records the intent and only matters once that hole is closed.
