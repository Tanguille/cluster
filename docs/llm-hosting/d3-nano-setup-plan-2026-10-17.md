# D3-nano replacement and rollout

Status, 2026-10-29: phase 2 is ready for merge and rollout approval. The branch carries main, the Qwen2B
retirement is split out to phase 3, and the Hermes plugins moved to the Hermes PVC (no repo plugin code).
The official CPU runtime image is public and digest-pinned.
The user's request to set up all useful candidates authorizes the local implementation steps below.

## Workspace

- Branch: `feat/d3-nano-preparation`, merged with `origin/main` on 2026-10-29.
- Worktree: `/home/tanguille/Documents/PriveProjecten/cluster/.worktrees/d3-nano-preparation`.
- Preserve unrelated copied `.Codex/` and `.vscode/` files; do not include them in commits.

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been consolidated into existing documentation, the plan file can be removed. If there is no relevant existing documentation, the plan should be reworked into a reference document.
- Every continuation prompt must verify the branch and worktree before any work: run `git status`, then `git pull --ff-only && git status`.
- Push and live reconciliation require explicit approval under [AGENTS.md](../../AGENTS.md).

## Runtime and architecture

The [publisher catalog](https://huggingface.co/vllm-sr/models), checked on 2026-10-17,
identifies D3 as the latest decision family and d3-nano as its approximately 2B offering.
The pinned [model package](https://huggingface.co/vllm-sr/d3-nano/tree/aeb70d4dd53ed25b855c3b0a66394b8fe265db7f)
is Apache-2.0, with 2,213,241,664 backbone parameters plus 522,240 readout parameters.
Its main weights occupy 4.12 GiB.

Use the publisher's native CPU worker, `vllm-srun`, from
[semantic-router revision 5bd7c69419487a783cbce18dfd41f707441a1b85](https://github.com/vllm-project/semantic-router/tree/5bd7c69419487a783cbce18dfd41f707441a1b85/src/model-runtime).
D3 needs its decision readout and noncausal attention; a llama.cpp chat-model substitution loses that behavior.
No Vulkan path has been established for our iGPU. Use the publisher's public
[`vllm-sr` CPU image](https://github.com/vllm-project/semantic-router/pkgs/container/semantic-router%2Fvllm-sr),
which includes `vllm-srun` and CPU Torch 2.10.0. The image tag matches the tested source revision and its
digest is pinned independently of the model revision. Override the router entrypoint with `command: ["vllm-srun"]`
to run only the native worker; no router process, custom image or publishing workflow is needed.

Hermes calls native `/v1/systemone` on port 8100. Two compatibility adapters share one ConfigMap source:
Kritika uses an authenticated confidence endpoint on 8101; Siren uses a localhost alert proxy on 8102.
There is no public route or general chat API. Cilium restricts native inference to Hermes/Siren,
confidence to agentgateway, and metrics to vmagent; the namespace-wide allowlist excludes D3 pods.
The adapters use the standard library and are reloaded when their ConfigMap changes.

## Useful integrations implemented

| Caller | D3 job | Preserved behavior |
| --- | --- | --- |
| Hermes | Tool-family hints and fast/reasoning choice | Existing tools resolve arguments and execute; explicit thinking settings and other providers are preserved |
| Hermes tools | Home Assistant intent, supplied bookmark categories, alert urgency | Advisory results; no device changes, bookmark edits or remediation |
| Memini provider | Gate automatic recall/capture | Explicit memory tools, lifecycle, disabled gates and uncertain/error cases retain upstream behavior |
| Kritika | Existing confidence tool's 0–5 score and risk enum | Full reviews remain generative; Kritika falls back to omniroute on failure |
| Siren | Cached alert category/urgency annotations | Alerts appear immediately; original severity, delivery and silence operations remain intact |

Hermes decisions require complete input, valid normalized probabilities and conservative confidence/margin checks.
Timeouts, oversized input and uncertain results preserve the existing workflow. Siren enrichment is asynchronous
and bounded; it cannot delay alert delivery. Kritika uses its actual `confidence` function-tool contract,
including `tool_calls`, rather than plain JSON content. Its requests must fit without truncation.

Qwen2B text generation cannot be replaced by a classifier. Karakeep summaries/tags and Hermes title generation
move to `qwen-3.8-fast`; decisions use D3. The Hermes side (`d3-decisions`, `d3-memini`, `configure_d3.py`)
lives on the Hermes PVC under `/opt/data/plugins/`, like every self-made Hermes plugin, installed by a
user-run apply script. `configure_d3.py` backs each config up once as `config.yaml.before-d3`, preserves
unrelated settings, and updates root plus every profile. The Memini wrapper inherits the installed provider,
including its local PVC patches. `d3-decisions/d3_client.py` is a copy of the component's `d3_client.py`.

## Evidence and capacity

- Official CPU image: native runtime 0.2.0, CPU Torch 2.10.0, golden checks 5/5; UID 65532 with a read-only root passed readiness and a real decision.
- Local BF16, four threads: startup peak RSS 7.35 GiB, steady RSS 4.65 GiB; a short decision took about 0.43–0.64 seconds.
- The [30 labeled examples and results](bench/d3-nano/local-results.json) cover routes 10/10,
  alert urgency 10/10, capture 4/5 and recall 4/5. The two misses had low confidence and preserve existing memory behavior.
  Each row selects its shared question schema by family. The official-image replay matched all 30 previous predictions
  with complete input coverage (four-CPU/10 GiB cap, slowest 1.39s). These synthetic cases do not calibrate production thresholds.
- A request with Kritika's real confidence system prompt used 3,489 input tokens and took 13.1 seconds locally.
  Central timeout is 30 seconds; agentgateway-retry re-sends 503s. Hermes/Siren use three-second decision budgets.
- Resource requests: native 6 GiB / 500m CPU, gateway 64 MiB / 50m CPU; native limits 10 GiB / four CPUs.
  Steady RSS (4.65 GiB) sets the request; the 7.35 GiB load peak fits under the limit. A two-CPU request would not schedule.
- Read-only snapshot, 2026-10-29: unrequested memory/CPU was control-1 11.1 GiB / 2.7 cores, control-2 0.6 GiB,
  control-3 2.5 GiB; actual use on control-1 was 33.2 of 59.3 GiB. Only control-1 fits. It also hosts
  the 27B model; node performance and interference remain unverified.
- Hermes v0.22.2 was Ready. Authenticated calls through its actual local inference configuration succeeded
  for fast and reasoning aliases. The migration was tested against all nine live configurations in memory:
  no remaining old aliases, primary models preserved, and no live writes. Full deployed D3 integration remains pending.
- Hermes logs contained earlier shutdown/tool-call errors; current inference success does not establish their absence historically.

## Delivery steps

### 1. Local implementation — complete

Native worker, pinned upstream image, integrations, migration, replacement aliases and final removal manifests are prepared.
Tests cover malformed results, unavailable inference, provider boundaries, memory preservation, config migration,
Kritika's tool contract and alert proxy behavior. Run repository validation, YAML schema checks and scoped tests before delivery.

Continuation: “Verify the D3 branch/worktree, finish final local validation and present the exact delivery scope.”

### 2. Enable consumers — awaiting rollout approval

Use `ghcr.io/vllm-project/semantic-router/vllm-sr:5bd7c69419487a783cbce18dfd41f707441a1b85` at
`sha256:6f6a2cd99677dedff4bbb7fdc41fb5597b8e8980489f8070eb741ffb0b8236c7` for runtime and gateway.
Anonymous image pull is verified; our private GHCR package and its settings are no longer dependencies.
The upstream image includes the exact native runtime revision used in the earlier CPU evaluation.
Deliver D3, Siren, agentgateway, Kritika and Karakeep changes while retaining the old Qwen model manifest;
`kubernetes/apps/ai/llmkube/` and the embedder comparison cleanup are not in this delivery.
Wait for CI and approval before reconciliation. Siren rolls its two replicas; agentgateway/Kritika/Karakeep
update normally. Once D3 is Ready, the user runs the Hermes apply script: it stages both plugins, runs their
self-checks with the pod's Python, installs them, runs `configure_d3.py` as `hermes`, and restarts the
gateway drain-aware with SIGUSR1 (`s6-svc -1`), so running cron turns finish first.
Verify image/revision/golden readiness, network allow/deny behavior, Hermes tools and memory, an actual bookmark
summary/tag job, confidence fallback, Siren alert/silence behavior and target-node load before retiring Qwen.

The rendered `cluster-apps` patch supplies `cluster-secrets` substitution to D3, as it does for every child
Kustomization. CPU thread arguments use [Kubernetes environment expansion](https://kubernetes.io/docs/tasks/inject-data-application/define-command-argument-container/).
The gateway's startup probe checks `/healthz` independently of native model loading; readiness waits for the worker.

Continuation: “Verify the D3 branch/worktree, verify the pinned upstream image and deliver the approved first phase, retaining Qwen2B.”

### 3. Retire Qwen and consolidate documentation — pending live checks

Deliver the llmkube deletion, placement/comment changes and comparison-doc cleanup only after phase 2 checks pass.
They are in this branch's history as `114f180df` (`git show 114f180df`), removed from the delivery on 2026-10-29.
Reconcile and verify the old workload is gone, all Hermes profiles use the replacements, and no active dependency remains.
Retain the pinned old model in git history for rollback. If consumers regress, revert their configuration changes first;
restore Hermes's `config.yaml.before-d3` backups only through an explicitly approved recovery operation.
Convert this document into the operating reference after verified rollout and record final image digest and measurements.

Continuation: “Verify the D3 branch/worktree and first-phase live evidence, then retire Qwen2B and finish the reference documentation.”
