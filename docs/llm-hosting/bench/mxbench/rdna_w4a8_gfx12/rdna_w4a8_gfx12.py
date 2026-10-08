# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""MXFP4 linear kernel for RDNA3/RDNA3.5 (gfx11) with an int8-activation decode
path.

gfx12 port of vllm-project/vllm#60413 (Apache-2.0, see NOTICE.md). Deltas against the PR:
  - is_supported: ``on_gfx11() or on_gfx12x()``
  - the opt-in env var is read from os.environ (the installed vLLM has no envs entry)
  - the op is ``torch.ops.mxw4a8.gemv`` from mxw4a8_ext.py (standalone build)

Routes on the number of activation rows M:
  1 <= M <= MAX_W4A8_BATCH_SIZE (decode, speculative verify):
      HIP W4A8 GEMV (``mxfp4_w4a8_gemv``). Activations are quantized
      to int8 per 32-element group and reduced with int8 dot instructions.
  otherwise (prefill, profiling):
      dequantize the weight and run a high-precision GEMM, the same math as
      ``EmulationMxfp4LinearKernel``.

Both paths read the checkpoint layout directly ([N, K/2] uint8 E2M1 codes,
[N, K/32] uint8 E8M0 scales), so there is no weight repack.

Opt-in with ``VLLM_ROCM_MXFP4_W4A8=1``: the int8 activation rounding is not the
numerics the checkpoint specifies.
"""

import os

import torch
import torch.nn.functional as F
from torch.nn.parameter import Parameter

import mxw4a8_ext
from vllm.model_executor.kernels.linear.mxfp4.base import (
    MxFp4LinearKernel,
    MxFp4LinearLayerConfig,
)
from vllm.model_executor.layers.quantization.utils.mxfp4_utils import (
    dequant_mxfp4,
    quant_dequant_mxfp4,
)
from vllm.model_executor.layers.quantization.utils.quant_utils import (
    kMxfp4Dynamic,
)
from vllm.platforms import current_platform
from vllm.utils.import_utils import has_quark
from vllm.utils.torch_utils import direct_register_custom_op

mxw4a8_ext.load_op()

MAX_W4A8_BATCH_SIZE = 8
_MXFP4_GROUP_SIZE = 32


def _w4a8_enabled() -> bool:
    # same parsing as the PR's VLLM_ROCM_MXFP4_W4A8 entry in vllm/envs.py
    return os.getenv("VLLM_ROCM_MXFP4_W4A8", "False").lower() in ("true", "1")


def _rdna_mxfp4_w4a8_apply_impl(
    x_2d: torch.Tensor,
    weight: torch.Tensor,
    weight_scale: torch.Tensor,
    bias: torch.Tensor | None,
    act_qdq: bool,
) -> torch.Tensor:
    M = x_2d.shape[0]
    if 0 < M <= MAX_W4A8_BATCH_SIZE and x_2d.dtype in (torch.float16, torch.bfloat16):
        out = mxw4a8_ext.gemv(x_2d, weight, weight_scale)
        if bias is not None:
            out.add_(bias)
        return out

    x_in = quant_dequant_mxfp4(x_2d) if act_qdq else x_2d
    return F.linear(x_in, dequant_mxfp4(weight, weight_scale, x_2d.dtype), bias)


def _rdna_mxfp4_w4a8_apply_fake(
    x_2d: torch.Tensor,
    weight: torch.Tensor,
    weight_scale: torch.Tensor,
    bias: torch.Tensor | None,
    act_qdq: bool,
) -> torch.Tensor:
    return torch.empty(
        (x_2d.shape[0], weight.shape[0]), dtype=x_2d.dtype, device=x_2d.device
    )


direct_register_custom_op(
    op_name="rdna_mxfp4_w4a8_apply",
    op_func=_rdna_mxfp4_w4a8_apply_impl,
    mutates_args=[],
    fake_impl=_rdna_mxfp4_w4a8_apply_fake,
)


class RdnaW4A8MxFp4LinearKernel(MxFp4LinearKernel):
    """MXFP4 GEMM for RDNA3/RDNA3.5: W4A8 int8-dot GEMV for M <= 8, dequant +
    high-precision GEMM above."""

    def __init__(self, config: MxFp4LinearLayerConfig) -> None:
        super().__init__(config)
        # Larger batches keep the checkpoint's MXFP4 activation QDQ, as the
        # emulation kernel does.
        self.act_qdq = config.activation_quant_key == kMxfp4Dynamic

    @classmethod
    def is_supported(
        cls, compute_capability: int | None = None
    ) -> tuple[bool, str | None]:
        if not current_platform.is_rocm():
            return False, "only targets ROCm"
        from vllm.platforms.rocm import on_gfx11, on_gfx12x

        if not (on_gfx11() or on_gfx12x()):
            return False, "requires RDNA3/RDNA3.5/RDNA4 (gfx11/gfx12)"
        if not _w4a8_enabled():
            return False, "disabled; set VLLM_ROCM_MXFP4_W4A8=1 to enable"
        if not hasattr(torch.ops.mxw4a8, "gemv"):
            return False, "mxw4a8::gemv is not built"
        return True, None

    @classmethod
    def can_implement(cls, config: MxFp4LinearLayerConfig) -> tuple[bool, str | None]:
        if config.activation_quant_key not in (None, kMxfp4Dynamic):
            return False, "only supports MXFP4 dynamic or unquantized activations"
        if not has_quark():
            return False, "amd-quark package not available (needed for M > 8)"
        return True, None

    def process_weights_after_loading(self, layer: torch.nn.Module) -> None:
        weight = layer.weight.data
        weight_scale = layer.weight_scale.data
        if weight.dtype != torch.uint8 or weight_scale.dtype != torch.uint8:
            raise ValueError(
                "RdnaW4A8MxFp4LinearKernel expects uint8 packed weights and "
                f"uint8 E8M0 scales, got {weight.dtype} and {weight_scale.dtype}"
            )
        N, k_half = weight.shape
        K = 2 * k_half
        if K % _MXFP4_GROUP_SIZE != 0 or weight_scale.shape != (
            N,
            K // _MXFP4_GROUP_SIZE,
        ):
            raise ValueError(
                "RdnaW4A8MxFp4LinearKernel expects weight [N, K/2] and "
                f"weight_scale [N, K/32] with K % 32 == 0, got "
                f"{tuple(weight.shape)} and {tuple(weight_scale.shape)}"
            )
        layer.weight = Parameter(weight.contiguous(), requires_grad=False)
        layer.weight_scale = Parameter(weight_scale.contiguous(), requires_grad=False)

    def apply_weights(
        self,
        layer: torch.nn.Module,
        x: torch.Tensor,
        bias: torch.Tensor | None = None,
    ) -> torch.Tensor:
        x_2d = x.reshape(-1, x.shape[-1])
        if not x_2d.is_contiguous():
            x_2d = x_2d.contiguous()
        out = torch.ops.vllm.rdna_mxfp4_w4a8_apply(
            x_2d, layer.weight, layer.weight_scale, bias, self.act_qdq
        )
        return out.reshape(*x.shape[:-1], out.shape[-1])
