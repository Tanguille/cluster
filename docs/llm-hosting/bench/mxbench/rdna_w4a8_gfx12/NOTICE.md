# Notice

`pr60413.diff`, `mxfp4_w4a8_gemv.cu` and `rdna_w4a8_gfx12.py` are derived from
[vllm-project/vllm#60413](https://github.com/vllm-project/vllm/pull/60413),
"[Perf][ROCm][Kernel] Add W4A8 int8-dot MXFP4 GEMV for RDNA3/RDNA3.5 decode".

| Field | Value |
| --- | --- |
| Upstream project | vLLM (`vllm-project/vllm`) |
| Pull request | #60413 (open when fetched, 2026-10-07) |
| Head sha | `af585139efe9946cfb1b7ccc9d2776d1edc552bb` |
| Base sha | `14e3902d5136e2fb00ef86c05fdc8d098f736283` |
| Licence | Apache-2.0 (`SPDX-License-Identifier: Apache-2.0`, "Copyright contributors to the vLLM project") |
| Fetched with | `gh pr diff 60413 -R vllm-project/vllm` and `gh pr view 60413 -R vllm-project/vllm --json headRefOid` |

The PR's own comment in the kernel source says the scheme follows ggml's MMVQ / q8_1 path
(llama.cpp, MIT License).

Local changes against the PR are listed at the top of each derived file. In short: the arch gate
and host check accept gfx12 as well as gfx11, `on_gfx11() or on_gfx12x()` replaces `on_gfx11()`,
the opt-in env var is read from `os.environ`, and the op is registered by a standalone build
as `torch.ops.mxw4a8.gemv`. The upstream licence header lines in the Python file are kept as is.
The `.cu` file in the PR carries no header of its own; it is covered by the PR's licence.

The rest of the directory (tests, benches, loader, fetch helper) is original to this repo.
