// Minimal stand-in for vLLM's csrc/cuda_compat.h (HIP branch): the one macro the PR's kernel uses.
#pragma once
#define VLLM_SHFL_XOR_SYNC_WIDTH(var, lane_mask, width) \
  __shfl_xor(var, lane_mask, width)
