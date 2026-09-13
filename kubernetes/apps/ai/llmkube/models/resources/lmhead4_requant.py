#!/usr/bin/env python3
"""Re-pack the qwen38-27b W4A16 checkpoint with a 4-bit lm_head.

lm_head is the only tensor the checkpoint keeps in bf16 (2.54 GB read every
decode step regardless of batch). Quality gate passed 2026-09-13 with the
same RTN math as a fake-quant hook (GSM8K 94.0 vs 93.3, McNemar p=1.0,
tools 12/12); this writes it for real so the bytes read per step drop.

Streams model.safetensors (18.7 GB) to a new file, copying every tensor
byte-for-byte except lm_head.weight, which is replaced by the four
compressed-tensors pack-quantized tensors. Group 128, asymmetric int4,
min/max observer, packed with compressed_tensors' own pack_to_int32 so the
layout is exactly what the other 311 linears use. CPU only, ~3 GB RAM.

Env: SRC (dir holding model.safetensors), DST (output dir). Idempotent: exits
0 immediately if DST/DONE exists.
"""
import json, os, shutil, struct

import torch
from compressed_tensors.compressors.pack_quantized.helpers import pack_to_int32

SRC, DST = os.environ["SRC"], os.environ["DST"]
G, ROWS = 128, 4096  # row block: 4096 x 5120 fp32 = 80 MB


def read_header(f):
    (n,) = struct.unpack("<Q", f.read(8))
    return json.loads(f.read(n)), 8 + n


def quantize_rows(w):
    # compressed_tensors.quantization.utils.helpers.calculate_qparams, int4
    # asymmetric: q in [-8, 7], zero always representable, minmax observer.
    N, K = w.shape
    g = w.float().reshape(N, K // G, G)
    mn = g.amin(-1).clamp(max=0.0); mx = g.amax(-1).clamp(min=0.0)
    scale = (mx - mn) / 15.0
    scale = torch.where(scale == 0, torch.full_like(scale, torch.finfo(torch.bfloat16).eps), scale)
    zp = torch.clamp(-8 - mn / scale, -8, 7).round()
    q = torch.clamp((g / scale[..., None]).round() + zp[..., None], -8, 7).to(torch.int8)
    deq = (q.float() - zp[..., None]) * scale[..., None]
    err = ((deq - g).norm() / g.norm()).item()
    return q.reshape(N, K), scale.to(torch.bfloat16), zp.to(torch.int8), err


def main():
    if os.path.exists(os.path.join(DST, "DONE")):
        print("already done")
        return
    os.makedirs(DST, exist_ok=True)
    for name in os.listdir(SRC):
        if name not in ("model.safetensors", "model.safetensors.index.json", "config.json"):
            shutil.copy2(os.path.join(SRC, name), DST)

    src = open(os.path.join(SRC, "model.safetensors"), "rb")
    hdr, base = read_header(src)
    meta = hdr.pop("__metadata__", None)
    lm = hdr.pop("lm_head.weight")
    assert lm["dtype"] == "BF16", lm
    N, K = lm["shape"]
    assert K % G == 0 and N % 8 == 0

    # quantize lm_head in row blocks
    src.seek(base + lm["data_offsets"][0])
    packed, scales, zps, errs = [], [], [], []
    for r0 in range(0, N, ROWS):
        rows = min(ROWS, N - r0)
        w = torch.frombuffer(src.read(rows * K * 2), dtype=torch.bfloat16).reshape(rows, K)
        q, s, zp, err = quantize_rows(w)
        packed.append(pack_to_int32(q, 4, packed_dim=1))       # [rows, K/8]
        zps.append(pack_to_int32(zp, 4, packed_dim=0))         # [rows/8, K/G]
        scales.append(s)
        errs.append(err)
    new = {
        "lm_head.weight_packed": torch.cat(packed).contiguous(),
        "lm_head.weight_scale": torch.cat(scales).contiguous(),
        "lm_head.weight_zero_point": torch.cat(zps).contiguous(),
        "lm_head.weight_shape": torch.tensor([N, K], dtype=torch.int64),
    }
    print(f"lm_head {N}x{K}: rel_err mean {sum(errs) / len(errs):.4f} max {max(errs):.4f}; "
          f"packed {new['lm_head.weight_packed'].shape} zp {new['lm_head.weight_zero_point'].shape}", flush=True)

    # new header: old order minus lm_head.weight, then the four new tensors
    out_hdr, off = {}, 0
    order = sorted(hdr.items(), key=lambda kv: kv[1]["data_offsets"][0])
    for name, t in order:
        n = t["data_offsets"][1] - t["data_offsets"][0]
        out_hdr[name] = {"dtype": t["dtype"], "shape": t["shape"], "data_offsets": [off, off + n]}
        off += n
    for name, t in new.items():
        n = t.numel() * t.element_size()
        dt = {torch.int32: "I32", torch.int64: "I64", torch.bfloat16: "BF16"}[t.dtype]
        out_hdr[name] = {"dtype": dt, "shape": list(t.shape), "data_offsets": [off, off + n]}
        off += n
    if meta:
        out_hdr["__metadata__"] = meta
    hb = json.dumps(out_hdr, separators=(",", ":")).encode()
    hb += b" " * (-len(hb) % 8)

    tmp = os.path.join(DST, "model.safetensors.part")
    with open(tmp, "wb") as dst:
        dst.write(struct.pack("<Q", len(hb)))
        dst.write(hb)
        for name, t in order:
            src.seek(base + t["data_offsets"][0])
            left = t["data_offsets"][1] - t["data_offsets"][0]
            while left:
                chunk = src.read(min(left, 64 << 20))
                if not chunk:
                    raise EOFError(f"source truncated inside {name}")
                dst.write(chunk)
                left -= len(chunk)
        for t in new.values():
            dst.write(t.contiguous().view(torch.uint8).numpy().tobytes())
    os.rename(tmp, os.path.join(DST, "model.safetensors"))

    idx = json.load(open(os.path.join(SRC, "model.safetensors.index.json")))
    wm = idx["weight_map"]
    f = wm.pop("lm_head.weight")
    for name in new:
        wm[name] = f
    idx["metadata"]["total_size"] = off
    json.dump(idx, open(os.path.join(DST, "model.safetensors.index.json"), "w"), indent=2)

    cfg = json.load(open(os.path.join(SRC, "config.json")))
    qc = cfg["quantization_config"]
    qc["ignore"] = [i for i in qc["ignore"] if i != "lm_head"]
    # vLLM matches targets by exact layer name or class-name substring; the
    # class is ParallelLMHead, so "Linear" alone would leave it unquantized.
    qc["config_groups"]["group_0"]["targets"].append("lm_head")
    json.dump(cfg, open(os.path.join(DST, "config.json"), "w"), indent=2)

    # verify: reopen through safetensors, compare the first copied tensor's bytes to the source
    from safetensors import safe_open
    with safe_open(os.path.join(DST, "model.safetensors"), "pt") as chk:
        assert len(list(chk.keys())) == len(out_hdr) - (1 if meta else 0)
        first = order[0][0]
        src.seek(base + hdr[first]["data_offsets"][0])
        assert chk.get_tensor(first).contiguous().view(torch.uint8).numpy().tobytes() == src.read(
            hdr[first]["data_offsets"][1] - hdr[first]["data_offsets"][0]), "byte copy mismatch"
        print("verify: header and first tensor bytes identical, keys", len(out_hdr))
    open(os.path.join(DST, "DONE"), "w").write("ok\n")


if __name__ == "__main__":
    main()
