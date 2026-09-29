#!/usr/bin/env python3
"""Does the ViT still SEE the image? A/B gate for a vLLM image or ViT-backend change.

The failure this catches is invisible to every other bench in this directory.
concsweep.py, longctx.py and toolbench.py send text only, so a model whose
vision encoder emits garbage still passes all of them at full speed: the ViT
runs on 0.92 GB of weights, only during profile_run and image requests, and
never touches the decode kernels those scripts measure. A change that breaks
photo recognition looks exactly like a healthy pod from the text benches.

So this asks the model questions with objectively checkable answers about a
fixed synthetic image, at temperature 0, and reports the score. Two runs --
one before the change, one after -- give the A/B. It needs no dataset, no
network and no reference model: the images are drawn here and the ground truth
is the drawing.

The questions deliberately span the discriminations a degraded ViT loses
first, in rough order of sensitivity:

  count    how many shapes        -- global aggregation over many patches
  color    which colour           -- fine detail, one patch region
  text     what does the sign say -- highest-frequency detail, OCR-like
  spatial  where is the marker   -- positional encoding / rope correctness
  identity what is this a picture of -- whole-image semantics

A ViT whose SDPA/Triton kernel is numerically wrong (vllm#30167 reports
flash_sdp and mem_efficient_sdp producing bad embeddings on ROCm; vllm#49851
reports the SDPA path erroring outright on gfx1201) fails `text` and `count`
first and `identity` last, so scoring per-category separates "the encoder is
broken" from "the model is just unsure".

Images are generated, not downloaded, so the suite is byte-identical on every
run and the two arms are comparable. Each is a solid mid-tone background with
high-contrast primitives; PNG bytes are deterministic for a given Pillow
version, and the questions do not depend on exact pixels anyway.

DETERMINISM: temperature 0 and a fixed seed, so a healthy engine returns the
same text twice. vLLM is not bitwise deterministic across restarts even so
(batch composition moves reduction order), so a single differing word is not
a failure -- compare the aggregate score and the per-category table between
arms, and treat a category dropping by more than one item as the signal.

Usage:
    python3 visioncheck.py PORT MODEL [OUT_JSON]

    PORT     port-forward target, e.g. 18000
    MODEL    served model name, e.g. qwen-3.8
    OUT_JSON optional path to write full results, for diffing two arms

Exit status is 0 whenever the endpoint answered every question. It is NOT a
pass/fail gate on its own: the pass criterion is "this arm's score and
per-category table are not worse than the baseline arm", which is a judgement
made across two runs, not something one run can decide.
"""
import base64
import io
import json
import sys
import urllib.error
import urllib.request

from PIL import Image, ImageDraw

URL = "http://127.0.0.1:{port}/v1/chat/completions"
PORT = int(sys.argv[1])
MODEL = sys.argv[2]
OUT = sys.argv[3] if len(sys.argv) > 3 else None

GEN = 48
SEED = 20260930
BG = (32, 34, 38)
SIZE = 448


def _canvas():
    img = Image.new("RGB", (SIZE, SIZE), BG)
    return img, ImageDraw.Draw(img)


def _three_shapes():
    img, d = _canvas()
    for i, colour in enumerate(((220, 60, 60), (60, 200, 90), (70, 120, 230))):
        x = 70 + i * 120
        d.ellipse([x, 150, x + 90, 240], fill=colour)
    return img


def _word_sign():
    img, d = _canvas()
    d.rectangle([40, 170, 408, 280], fill=(250, 250, 250))
    for i, ch in enumerate("QUAY"):
        d.text((70 + i * 85, 200), ch, fill=(10, 10, 10))
        # thicken the glyph: the default bitmap font is thin at this size
        for dx in (1, 2):
            for dy in (1, 2):
                d.text((70 + i * 85 + dx, 200 + dy), ch, fill=(10, 10, 10))
    return img


def _red_marker():
    img, d = _canvas()
    d.rectangle([0, 0, SIZE - 1, 120], fill=(45, 48, 55))
    d.rectangle([0, 330, SIZE - 1, SIZE - 1], fill=(45, 48, 55))
    d.ellipse([190, 190, 260, 260], fill=(230, 40, 40))
    return img


def _sunset():
    img, d = _canvas()
    for y in range(150, 330):
        t = (y - 150) / 180
        d.line([(0, y), (SIZE, y)],
               fill=(int(250 - 120 * t), int(120 + 60 * t), int(60 + 40 * t)))
    d.ellipse([300, 70, 380, 150], fill=(255, 240, 160))
    d.rectangle([0, 330, SIZE - 1, SIZE - 1], fill=(20, 24, 20))
    return img


def _colour_tile():
    img, d = _canvas()
    d.rectangle([110, 110, 340, 340], fill=(250, 210, 40))
    return img


# (name, category, builder, question, acceptable answers)
# "acceptable" is lowercase substring matching against the model's reply, so a
# correct answer phrased differently ("three" vs "3") still scores.
CASES = [
    ("three-dots", "count", _three_shapes,
     "How many shapes are in this image? Answer with just the number.",
     ("three", "3")),
    ("quay-sign", "text", _word_sign,
     "What word is written on the white sign? Answer with just the word.",
     ("quay",)),
    ("red-marker", "spatial", _red_marker,
     "A red circle sits in the middle of three horizontal bands: a dark band "
     "at the top, the main area, and a dark band at the bottom. Which band "
     "contains the red circle? Answer with one word: top, middle, or bottom.",
     ("middle", "centre", "center")),
    ("sunset", "identity", _sunset,
     "What is this a picture of? Answer in a few words.",
     ("sunset", "sunrise", "dusk", "dawn", "landscape", "sky", "sun", "horizon",
      "evening", "sunset scene")),
    ("yellow-tile", "color", _colour_tile,
     "What colour is the large square in the middle of this image? Answer "
     "with one word.",
     ("yellow", "gold", "golden")),
]


def encode(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def ask(img_b64, question):
    body = json.dumps({
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ],
        }],
        "max_tokens": GEN,
        "temperature": 0.0,
        "seed": SEED,
    }).encode()
    req = urllib.request.Request(
        URL.format(port=PORT), data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        payload = json.loads(r.read().decode())
    return payload["choices"][0]["message"]["content"] or ""


def main():
    results, failures = [], 0
    for name, cat, build, question, accept in CASES:
        try:
            reply = ask(encode(build()), question)
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
                TimeoutError) as exc:
            # A vision-path error surfaces here as a 500 or a dropped
            # connection, which is the vllm#49851 signature. Report it as a
            # failed case rather than aborting: the remaining categories still
            # say whether the encoder is broken or merely unreachable.
            print(f"[ERROR] {name}: {exc}")
            results.append({"name": name, "category": cat, "reply": "",
                            "ok": False, "error": str(exc)})
            failures += 1
            continue
        low = reply.strip().lower()
        ok = any(a in low for a in accept)
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<13} ({cat:<8}) "
              f"{reply.strip()[:100]!r}")
        results.append({"name": name, "category": cat, "reply": reply.strip(),
                        "ok": ok})

    by_cat = {}
    for r in results:
        agg = by_cat.setdefault(r["category"], [0, 0])
        agg[1] += 1
        agg[0] += 1 if r["ok"] else 0

    print("\nby category:")
    for cat in sorted(by_cat):
        good, total = by_cat[cat]
        print(f"  {cat:<9} {good}/{total}")

    good = sum(1 for r in results if r["ok"])
    print(f"\nvision score: {good}/{len(results)}"
          f"   errors: {failures}")

    if OUT:
        with open(OUT, "w") as fh:
            json.dump({"model": MODEL, "port": PORT, "score": good,
                       "total": len(results), "results": results}, fh, indent=2)
        print(f"wrote {OUT}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
