// Standalone registration of the PR's op. vLLM registers it as _rocm_C::mxfp4_w4a8_gemv inside
// csrc/rocm/torch_bindings.cpp; the bench build uses its own namespace so it cannot collide with
// the installed vLLM's _rocm_C library.
#include <torch/library.h>
#include <torch/all.h>

torch::Tensor mxfp4_w4a8_gemv(const at::Tensor& a, const at::Tensor& b_q,
                              const at::Tensor& b_scale);

TORCH_LIBRARY(mxw4a8, m) {
  m.def("gemv(Tensor a, Tensor b_q, Tensor b_scale) -> Tensor");
  m.impl("gemv", torch::kCUDA, &mxfp4_w4a8_gemv);
}
