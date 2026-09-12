# gfx1201 fast/slow band: the "nightly regression" was a spawn lottery

Single R9700 (gfx1201), kernel 7.1.9, ROCm 7.2.3 userspace, qwen38-27b-vllm
(W4A16 GDN hybrid, AITER unified attention, KV offload connector).

The investigation that pinned `0d07767` and listed 84 vLLM commits as suspects
is in `spec-decode-rejected.md`, "Unrelated finding: the nightly regression
persists". None of those commits was the cause.

## What it is

A per-process bistable state of the GPU, ROCm/ROCm#6347. Measured on nightly
`70d3eb5` (v0.28.1rc1.dev681) with the AITER overlay, idle server, one request
at a time (`longctx.py 18000 qwen-3.8 4000 1`, 511 tokens):

| state | tok/s @5K | ITL ms | mem_busy_percent | sclk MHz | power |
| --- | --- | --- | --- | --- | --- |
| fast | 32.6 | 30.7 | 81 | 3220 | 250 W |
| slow | 16.2 | 61.5 | 40 | 3450 | 250 W |

Exactly 2.00x. The state is process-wide (64K requests read 29.4 fast / 15.5
slow), sticky across requests, and flips at random moments in both directions;
a slow process recovered on its own once, which the upstream thread says never
happens on their boxes.

Draw rate is what differs between builds, not the kernels:

| image | spawns | fast |
| --- | --- | --- |
| `0d07767` (09-04, pinned) | 5 | 5 (two spent their first minute slow) |
| every nightly 09-05 .. 09-11 | 5 | 0 |
| `70d3eb5` + `GPU_MAX_HW_QUEUES=1` | 3 | 1 clean, 2 mixed (see below) |
| `960228c` (09-12) + `GPU_MAX_HW_QUEUES=1` | 1 | 1 (5K x3 31.9, 64K 28.7, tool calling 31/66/98, 48K conc 2-5 45/51/62/69) |

A 10-minute passive sample of the nightly under production traffic
(`mem_busy_percent` whenever `vllm:num_requests_running` was 1) gave 0 of 37
samples fast. With the env var, spawn 1's own engine log gave 80 of 81
single-request 10-second windows fast over 50 minutes; spawn 3 flipped
mid-request with only bench traffic on the server (5K probes 31 / 23 / 29 /
32 / 32 / 31 tok/s, 64K 27.7 and 23.2, `longconcsweep.py 38000` conc 2-5
aggregate 29 / 23 / 56 / 48 vs 46 / 51 / 63 / 73 on the pinned build). The
variable shifts the odds; it does not pin the band.

## Mechanism (inferred) and the fix

The exact 2x with every physical counter reading "half loaded" is a duty
cycle, not a slowdown. The host KFD view (`/sys/class/kfd/kfd/proc/<pid>/queues`)
showed the engine holding 5 compute + 2 SDMA hardware queues; RDNA4's MES
firmware timeslices queues, and a sibling queue spinning on nothing costs
half the GPU. `GPU_MAX_HW_QUEUES=1` reduces the process to 2 compute + 1 SDMA
queues (3 in the KFD view), which is why it helps without curing: one sibling
compute queue remains. The same variable is the llama.cpp workaround for the
RDNA4 MES multi-queue idle bug (ggml-org/llama.cpp#23965, ROCm/ROCm#5706). It
changes how many hardware queues the HIP runtime creates, nothing in the
kernels. Open lead: the engine also runs a second thread spinning at 100% CPU
even when idle (pure busy-wait by its context-switch counters); it could not
be identified without ptrace.

## Guard

Since the variable only shifts the odds, the liveness probe on the serving
container also classifies the band itself: every 30 s it reads its own
`/metrics` and the GPU's sysfs (readable in-container, no hostPath), and if
exactly one request is running and sclk is boosted (>= 3100 MHz) it records
`mem_busy_percent` >= 60 as a fast sample. Eight samples with fewer than a
quarter fast fail the probe and kubelet restarts the container.

The sclk gate is what excludes prefill: a chunked 50K prefill runs at
2480-2800 MHz with mem_busy 25-33, decode at 3180+ in both bands. Two gates
were tried and dropped first: `prompt_tokens_total` unchanged since the last
probe (the counter only moves when prefill ends, so the prefill itself passed
the gate), and `iteration_tokens_total` le=1.0 delta equal to count delta
(the histogram is flushed lazily and often has no delta inside a 30 s probe
period, and it says nothing about the sampling instant, so a request that
ended just before the probe was sampled against an idle GPU: four false slow
samples in one afternoon). Tested in the live container: no samples while
idle, through a 60 s prefill, or right after a request ends; fast samples in
solo decode; a forced 1-of-8 window fails.

## Ruled out

| dimension | fast vs slow |
| --- | --- |
| mclk / fclk / PCIe / temperatures / undervolt / power cap | identical (1258 MHz, 2016 MHz, 32 GT/s x16, junction 84 C, -82 mV, 250 W) |
| host | node 30% CPU, engine thread on its own core, 12 vCPUs |
| scheduler | `vllm:iteration_tokens_total` one token per iteration, 0 preemptions |
| cudagraph | `CudagraphDispatcher.dispatch` deterministic for one decode request |
| thread pinning | never tested cleanly (production traffic contaminated the run) |
| `--attention-backend TRITON_ATTN` (pinned build, 64K) | 1.9 tok/s, 456 s TTFT, JIT recompiles mid-serving; not an alternative in either band |

## Benchmark rule added

After any restart, classify the band before quoting a number: one request in
flight and `mem_busy_percent` >= 60 (or ITL ~31 ms at 5K) is fast. A slow
reading is a lottery loss, not a regression, until the same config has been
spawned fast at least once.
