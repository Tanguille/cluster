"""Synthetic checkpoint through lmhead4_requant.py, then check vLLM's unpack reproduces the RTN levels."""
import json, os, subprocess, sys, tempfile

import torch
from safetensors.torch import save_file, load_file

d = tempfile.mkdtemp(); src = os.path.join(d, "src"); dst = os.path.join(d, "dst"); os.makedirs(src)
N, K = 64, 256
t = {
    "model.a.weight_packed": torch.randint(-2**31, 2**31 - 1, (8, 32), dtype=torch.int32),
    "lm_head.weight": torch.randn(N, K, dtype=torch.bfloat16),
    "model.b.weight_scale": torch.randn(8, 2, dtype=torch.bfloat16),
    "model.b.weight_shape": torch.tensor([8, 256]),
}
save_file(t, os.path.join(src, "model.safetensors"), metadata={"format": "pt"})
json.dump({"metadata": {"total_size": 1}, "weight_map": {k: "model.safetensors" for k in t}},
          open(os.path.join(src, "model.safetensors.index.json"), "w"))
json.dump({"quantization_config": {"ignore": ["lm_head", "x"], "config_groups": {"group_0": {"targets": ["Linear"]}}}},
          open(os.path.join(src, "config.json"), "w"))
open(os.path.join(src, "tokenizer.json"), "w").write("{}")

r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "../../../kubernetes/apps/ai/llmkube/models/resources/lmhead4_requant.py")], env={**os.environ, "SRC": src, "DST": dst},
                   capture_output=True, text=True)
print(r.stdout); print(r.stderr[-2000:]); assert r.returncode == 0

o = load_file(os.path.join(dst, "model.safetensors"))
for k in ("model.a.weight_packed", "model.b.weight_scale", "model.b.weight_shape"):
    assert torch.equal(o[k], t[k]), k
assert "lm_head.weight" not in o
assert os.path.exists(os.path.join(dst, "tokenizer.json"))
cfg = json.load(open(os.path.join(dst, "config.json")))["quantization_config"]
assert cfg["ignore"] == ["x"] and cfg["config_groups"]["group_0"]["targets"] == ["Linear", "re:.*lm_head$"]
idx = json.load(open(os.path.join(dst, "model.safetensors.index.json")))["weight_map"]
assert "lm_head.weight_packed" in idx and "lm_head.weight" not in idx

# vLLM-side unpack, as RDNAHybridW4A16LinearKernel.process_weights_after_loading does it
from vllm.model_executor.layers.quantization.utils.quant_utils import unpack_quantized_values_into_int32
from vllm.scalar_type import scalar_types
q = unpack_quantized_values_into_int32(o["lm_head.weight_packed"], scalar_types.uint4, packed_dim=1)
zp = unpack_quantized_values_into_int32(o["lm_head.weight_zero_point"], scalar_types.uint4, packed_dim=0)
s = o["lm_head.weight_scale"].float()
assert q.shape == (N, K) and zp.shape == (N, K // 128), (q.shape, zp.shape)
deq = (q.float() - zp.float().repeat_interleave(128, 1)) * s.repeat_interleave(128, 1)
w = t["lm_head.weight"].float()
rel = ((deq - w).norm() / w.norm()).item()
print("vLLM-unpacked dequant rel_err", rel, "uint4 range", q.min().item(), q.max().item())
assert rel < 0.15 and 0 <= q.min() and q.max() <= 15
print("OK")
