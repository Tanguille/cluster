# D3-nano replacement and rollout

Status, 2026-10-29: phase 1 (local implementation) is complete, reworked onto the upstream vLLM Semantic
Router with no custom code. Awaiting merge and rollout approval. The Qwen2B retirement stays split out to phase 3.

## Workspace

- Branch: `feat/d3-nano-preparation`, merged with `origin/main` on 2026-10-29.
- Worktree: `/home/tanguille/Documents/PriveProjecten/cluster/.worktrees/d3-nano-preparation`.

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been consolidated into existing documentation, the plan file can be removed. If there is no relevant existing documentation, the plan should be reworked into a reference document.
- Every continuation prompt must verify the branch and worktree before any work: run `git status`, then `git pull --ff-only && git status`.
- Push and live reconciliation require explicit approval under [AGENTS.md](../../AGENTS.md).

## Architecture

The [publisher catalog](https://huggingface.co/vllm-sr/models), checked on 2026-10-17, identifies D3 as the
latest decision family and d3-nano as its approximately 2B offering. The pinned
[model package](https://huggingface.co/vllm-sr/d3-nano/tree/aeb70d4dd53ed25b855c3b0a66394b8fe265db7f) is
Apache-2.0; its main weights occupy 4.12 GiB. D3 needs its decision readout, so it runs in the publisher's
`vllm-srun` runtime rather than as a llama.cpp chat model.

[`kubernetes/apps/ai/semantic-router`](../../kubernetes/apps/ai/semantic-router/app/helmrelease.yaml) deploys the
publisher's Helm chart with config only. The router runs as an ExtProc service
([upstream agentgateway guide](https://github.com/vllm-project/semantic-router/blob/5bd7c69419487a783cbce18dfd41f707441a1b85/website/docs/installation/k8s/agentgateway.md))
and supervises d3-nano on CPU in the same pod. Clients ask agentgateway for `vllm-sr/auto`; a `PreRouting`
ExtProc policy ([`vllm-sr-auto.yaml`](../../kubernetes/apps/ai/agentgateway/app/models/vllm-sr-auto.yaml)) sends
only those requests to the router, which asks D3 whether the request can be answered directly and rewrites
the model to `qwen-3.8-fast` (confident yes) or `qwen-3.8` (anything else, including a late or failed answer).
agentgateway then routes the rewritten model normally, so each model keeps its own sampling and thinking
policies. If the router is unreachable, `vllm-sr/auto` fails over to `qwen-3.8`.

The chart is a nightly: v0.4.0, the latest release, predates D3, `decision` signals and the managed
`model_runtime`. Move to the first release that has them.

## Evidence and capacity

- Local run of the pinned image on the flate-rendered config, four CPUs, 10 GiB cap: d3-nano downloaded and
  became ready in about a minute. `POST /api/v1/routing/preview` sent two simple prompts to `qwen-3.8-fast`
  and a Ceph-debugging and a coding prompt to `qwen-3.8` in 0.54–0.70 s; a 64 KiB Hermes-shaped request
  (long system prompt, 40 tools, 12 prior turns) routed the same way. That laptop CPU has AVX-512 BF16;
  control-1's Ryzen 7 5800X does not, so expect slower decisions there.
- Earlier `vllm-srun` evaluation, BF16 on four threads: startup peak RSS 7.35 GiB, steady RSS 4.65 GiB.
- Read-only snapshot, 2026-10-29: unrequested memory/CPU was control-1 11.1 GiB / 2.7 cores, control-2 0.6 GiB,
  control-3 2.5 GiB; actual use on control-1 was 33.2 of 59.3 GiB. Only control-1 fits the 6 GiB request.
  It also hosts the 27B model; interference remains unverified.
- Not verified locally: the agentgateway ExtProc hook (PreRouting, conditional, body rewrite before model
  selection). The upstream guide covers PostRouting on agentgateway 1.4; we run 1.7.

## Delivery steps

### 2. Router rollout and Hermes cutover: awaiting merge and rollout approval

Merging reconciles the router, the agentgateway model and policy, the network policies and Karakeep, while the
old Qwen model stays. Verify, in order:

1. The router pod is Ready on control-1 and `GET /api/v1/inventory/model-runtime` (port-forward 8080) shows `d3` ready.
2. `POST /api/v1/routing/preview` on control-1 stays well under the 3 s decision budget. If not, try `vllm-sr/d3-lite`.
3. A `vllm-sr/auto` chat request through agentgateway answers. A simple prompt returns no reasoning content and a
   hard one does. `x-gateway-model` and agentgateway metrics may show `vllm-sr/auto` instead of the rewritten model.
4. With the router scaled to zero, `vllm-sr/auto` still answers through the `qwen-3.8` failover.
5. Another namespace cannot reach the router on 8080 or 50051.
6. A real Karakeep bookmark summary/tag job succeeds on `qwen-3.8-fast`.

Then point Hermes's main model at `vllm-sr/auto` in its PVC config (root and profiles), keep auxiliary tasks on
explicit models so they skip the router, and disable the `qwen38-effort-router` plugin, whose fast/reasoning
choice the router replaces. Restart drain-aware with SIGUSR1.

Continuation: “Verify the D3 branch/worktree, reconcile the semantic router and run the phase 2 checks.”

### 3. Retire Qwen and consolidate documentation: pending live checks

Deliver the llmkube deletion, placement/comment changes and comparison-doc cleanup only after phase 2 checks pass.
They are in this branch's history as `114f180df` (`git show 114f180df`). Move Hermes title generation from
`qwen35-2b` to `qwen-3.8-fast` first. Reconcile and verify the old workload is gone and no active dependency
remains. Retain the pinned old model in git history for rollback. Convert this document into the operating
reference after verified rollout.

Continuation: “Verify the D3 branch/worktree and phase 2 live evidence, then retire Qwen2B and finish the reference documentation.”
