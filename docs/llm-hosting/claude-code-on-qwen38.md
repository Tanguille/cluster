# Claude Code on self-hosted qwen-3.8 via litellm

Client-side only. No cluster change. Verified 2026-09-12 with Claude Code 2.1.269.

## Setup

litellm already exposes an Anthropic-format endpoint at
`https://litellm.${SECRET_DOMAIN}` (in-cluster `litellm.ai.svc.cluster.local`),
with aliases `qwen-3.8` (thinking) and `qwen-3.8-fast` (no thinking) from
`kubernetes/apps/ai/litellm/instance/models.yaml`. Claude Code only needs env:

```fish
# ~/.config/fish/functions/claude-qwen.fish (chmod 600, holds a litellm virtual key)
# Substitute the cluster domain for ${SECRET_DOMAIN} and the key for the <...> placeholder.
# Use a virtual key limited to qwen-3.8 and qwen-3.8-fast; the master key reaches every model group.
function claude-qwen
    CLAUDE_CODE_MODEL_CAPABILITIES="qwen*=-mid_conv_system,-mid_conv_tool_change" \
    ANTHROPIC_BASE_URL=https://litellm.${SECRET_DOMAIN} \
    ANTHROPIC_AUTH_TOKEN=<scoped litellm virtual key> \
    ANTHROPIC_MODEL=qwen-3.8 \
    ANTHROPIC_DEFAULT_SONNET_MODEL=qwen-3.8 \
    ANTHROPIC_DEFAULT_OPUS_MODEL=qwen-3.8 \
    ANTHROPIC_DEFAULT_HAIKU_MODEL=qwen-3.8-fast \
    claude $argv
end
```

Smoke test: `claude-qwen -p "What is 2+2? Reply with the number only." --max-turns 1` → `4`.

## The 400 and why `CLAUDE_CODE_MODEL_CAPABILITIES` is there

Symptom: every turn after the first fails with
`400 litellm.BadRequestError: OpenAIException - {"error":{"message":"System message must be at the beginning."}}`.

Cause: Claude Code 2.1.x inserts a `role:"system"` message *inside* `messages`
after the first user turn (SessionStart hook output and its `# Environment`
block). Qwen3.8's `chat_template.jinja` raises on any non-first system message,
and the request reaches vLLM as `/v1/responses` with the roles preserved.
Captured with a local stub server (`ANTHROPIC_BASE_URL=http://127.0.0.1:4001`,
dump the body, reply 500): roles were `['user', 'system']` for `qwen-3.8`.

Things that do NOT fix it:

- `disableAllHooks` (the environment block is still sent)
- `ANTHROPIC_DEFAULT_*_MODEL_SUPPORTED_CAPABILITIES` (skipped when the provider
  is firstParty, which `ANTHROPIC_BASE_URL` still is)
- Claude Code's own fallback for rejected system roles (only triggers on
  Anthropic/Bedrock-shaped error text, not vLLM's)
- litellm `supports_system_messages=false` (only honoured for o-series/Vertex)

Fix: the undocumented `CLAUDE_CODE_MODEL_CAPABILITIES` env, format
`model-glob=cap,-cap;...`, `-` negates, not provider-gated.
`qwen*=-mid_conv_system,-mid_conv_tool_change` drops the mid-conversation
system turn; recapture showed roles `['user']` only. Cost: that turn carries the
SessionStart hook output and `# Environment` block, so from the second turn on the
model no longer sees them.

The server-side alternative (patch the chat template via ConfigMap +
`--chat-template`, same overlay pattern as `lds-gate-patch` in
`kubernetes/apps/ai/llmkube/models/`) renders fine but rolls the vLLM pod, and
a restart stalls admission for hours (`vllm-optimization-log-2026-08.md`,
"A restart's real cost"). Not worth it for a client bug.

## What works, measured

Debug-log runs (`claude-qwen -p ... --permission-mode auto --debug-file /tmp/claude-qwen-debug.log`):

| feature | status | evidence |
| --- | --- | --- |
| tool calls, hooks, plugins, MCP, subagents, compaction, auto-memory | work | client-side; same run as below |
| auto mode | works; classifier always runs on the main model (`CLAUDE_CODE_AUTO_MODE_MODEL=qwen-3.8-fast` and `ANTHROPIC_DEFAULT_SONNET_MODEL=qwen-3.8-fast` both left `model=qwen-3.8` in the log) | `classifier_request_started model=qwen-3.8 stage=xml_s1`; cold call hit the 60s wall clock → `fail closed` deny; retries 10 to 35s (prefix cached) → `behavior=allow` |
| context window | 180K as seen by Claude Code | `autocompact: ... effectiveWindow=180000` (vLLM serves 246944) |
| first-turn latency | 58s on a trivial prompt | 10:37:41 request → 10:38:39 tool dispatch; ~35K-token system prompt prefill + thinking on the shared R9700 |
| prompt caching | vLLM prefix cache, `cache_control` ignored | see auto mode retries |
| Anthropic usage / telemetry | none | `[Anthropic telemetry] ... No API key available` |

Not available: WebSearch (Anthropic server tool), fast mode, Artifacts,
Remote Control, cloud code review, 1M context.

## Gotchas

- fish autoloads the function once per shell; after editing it, open a new
  shell or `source` the file, or the old env (and the 400) comes back.
- `qwen-3.8` with a tiny `max_tokens` returns empty content (thinking eats the
  budget). Claude Code never sets it that low; ad-hoc curls should use ≥512.
- Tool-call formatting errors are more frequent than with Claude; a
  `python3 -c '...'` probe had its quoting mangled and failed once.
