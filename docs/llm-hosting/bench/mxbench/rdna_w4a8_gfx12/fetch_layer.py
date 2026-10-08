#!/usr/bin/env python3
"""Range-fetch one MXFP4 linear layer (weight + weight_scale) out of the Swift checkpoint.

Only the safetensors header and the two tensors' byte ranges are downloaded (a few MB of the
19.8 GB file). Writes <out>.safetensors with keys "weight" (uint8 [N,K/2]) and "weight_scale"
(uint8 [N,K/32]).

  python3 fetch_layer.py --list attn            # list tensor names containing "attn"
  python3 fetch_layer.py --prefix model.language_model.layers.3.self_attn.o_proj --out o_proj
"""
import argparse, json, struct, urllib.request

REPO = "ethanwtodd/Swift-1.5-Qwen3.8-27b-Quark-RTN-MXFP4"
REV = "459bc7fe8340e75dbd22701692db909e21917dc7"
URL = f"https://huggingface.co/{REPO}/resolve/{REV}/model.safetensors"


def rng(a, b):
    req = urllib.request.Request(URL, headers={"Range": f"bytes={a}-{b}"})
    with urllib.request.urlopen(req) as r:
        data = r.read()
    assert len(data) == b - a + 1, (len(data), b - a + 1)
    return data


def header():
    n = struct.unpack("<Q", rng(0, 7))[0]
    return n, json.loads(rng(8, 8 + n - 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list")
    ap.add_argument("--prefix")
    ap.add_argument("--out", default="layer")
    a = ap.parse_args()
    n, h = header()
    base = 8 + n
    if a.list:
        for k, v in h.items():
            if a.list in k:
                print(k, v.get("dtype"), v.get("shape"))
        return
    out = {}
    for suffix, key in (("weight", "weight"), ("weight_scale", "weight_scale")):
        name = f"{a.prefix}.{suffix}"
        v = h[name]
        lo, hi = v["data_offsets"]
        assert v["dtype"] == "U8", v
        out[key] = (v["shape"], rng(base + lo, base + hi - 1))
        print(name, v["dtype"], v["shape"], hi - lo, "bytes")
    # minimal safetensors writer (no extra dependency): header + concatenated raw bytes
    off, hdr = 0, {}
    for k, (s, d) in out.items():
        hdr[k] = {"dtype": "U8", "shape": s, "data_offsets": [off, off + len(d)]}
        off += len(d)
    hb = json.dumps(hdr).encode()
    hb += b" " * (-len(hb) % 8)
    with open(a.out + ".safetensors", "wb") as f:
        f.write(struct.pack("<Q", len(hb)))
        f.write(hb)
        for _, d in out.values():
            f.write(d)
    print("wrote", a.out + ".safetensors")


if __name__ == "__main__":
    main()
