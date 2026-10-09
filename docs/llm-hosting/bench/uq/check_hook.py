"""Routing check for the live UQ_FAST hook (no vllm, no GPU).

  python check_hook.py [hook_src]

hook_src defaults to $UQ_HOOK_SRC, then the repo copy (when run from a checkout), then
./zz_lds_gate_impl.py, then the production hook mounted in the pod (which may lag the repo).
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REL = "kubernetes/apps/ai/llmkube/models/resources/zz_lds_gate_impl.py"
CANDIDATES = [
    os.environ.get("UQ_HOOK_SRC", ""),
    next((str(p / REL) for p in HERE.parents if (p / REL).exists()), ""),  # the repo checkout, when run from it
    str(HERE / "zz_lds_gate_impl.py"),  # a copy of the repo file next to this script (kubectl cp into the pod)
    "/usr/local/lib/python3.12/dist-packages/zz_lds_gate_impl.py",  # the hook mounted in the bench pod
]


def load(path, env):
    """Import the hook file as a fresh module under env (the real hook may already be loaded by the .pth)."""
    saved = {k: os.environ.get(k) for k in ("UQ_FAST",)}
    for k in saved:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(f"zz_hook_{len(sys.modules)}", path)
        H = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(H)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, H._Finder)]
    return H


class Impl:
    sinks, sliding_window, scale = None, None, 0.0625

    def _ultraquant_continuation_prefill(self, *, layer, **kw):
        return ("orig", layer)


def run(H, expect, calls):
    """expect: 'on' = UQ_FAST=1 (chunks up to 128 tokens go to uq_prefill_fast), 'off' = UQ_FAST=0."""
    Impl._ultraquant_continuation_prefill = lambda self, *, layer, **kw: ("orig", layer)
    mod = types.SimpleNamespace(
        UltraQuantAttentionImpl=Impl,
        ultraquant_unified_attention=lambda *a, **kw: calls.append(("upstream", kw)) or "upstream",
        _CONTINUATION_DECODE_THRESHOLD=128,
    )
    H._patch_uq(mod)
    impl = Impl()
    cont = dict(key_chunk=None, val_chunk=None, kv_cache="KV", block_table="BT", cached_len=900, PiT="P")

    def route(n, d=256):
        got = impl._ultraquant_continuation_prefill(
            layer="L", query=types.SimpleNamespace(shape=(n, 24, d)), seq_len=900 + n, **cont)
        if got == "prefill":
            return "prefill"
        assert got[0] == "orig" and got[1] != "L", got  # shared holder, never the per-layer object
        return "orig"

    maxq = {"on": 128, "off": 0}[expect]
    assert mod._CONTINUATION_DECODE_THRESHOLD == (128 if expect == "off" else 0)
    for n in (2, 3, 127, 128, 129, 130, 1536, 4096, 8192):
        want = "prefill" if n <= maxq else "orig"
        assert route(n) == want, (expect, n, route(n), want)
    if maxq:
        route(maxq)  # the last routed call carries the chunk, cache, table, cached_len, scale and PiT through
        assert calls[-1][0] == "prefill" and calls[-1][2] == {"PiT": "P"} and calls[-1][1][1:] == ("KV", "BT", 900, 0.0625), calls[-1]
    # Ineligible chunks stay on upstream whatever their size.
    impl.sinks = "s"
    assert route(8) == "orig" and route(200) == "orig"
    impl.sinks, impl.sliding_window = None, 128
    assert route(8) == "orig" and route(200) == "orig"
    impl.sliding_window = None
    assert route(8, d=128) == "orig" and route(200, d=128) == "orig"

    # Decode wrapper.
    q = types.SimpleNamespace(shape=(1, 24, 256))
    base = dict(query=q, kv_cache=types.SimpleNamespace(shape=(10, 64, 4, 272)), block_table=None, seq_lens=None,
                query_start_loc=types.SimpleNamespace(shape=(2,)), scale=0.0625, PiT="P", output="O", max_seq_len=100)
    f = mod.ultraquant_unified_attention
    if expect == "off":
        assert f(**base, max_query_len=1) == "upstream" and calls[-1][1]["tile_size"] == 32
    else:
        assert f(**base, max_query_len=1, sinks=None, sliding_window=None) == "fast"
        assert calls[-1][1] == dict(PiT="P", output="O", max_seq_len=100), calls[-1]
        assert f(**base, max_query_len=1, sinks="s", sliding_window=None) == "upstream"
        assert calls[-1][1]["tile_size"] == 32 and calls[-1][1]["num_kv_splits"] == 32  # geometry wrapper still applies
        assert f(**base, max_query_len=1, sinks=None, sliding_window=128) == "upstream"
        assert f(**dict(base, query=types.SimpleNamespace(shape=(1, 24, 128))), max_query_len=1) == "upstream"


def run_all(path):
    calls = []
    sys.modules["uq_decode_fast"] = types.SimpleNamespace(
        uq_decode_fast=lambda *a, **kw: calls.append(("fast", kw)) or "fast",
        uq_prefill_fast=lambda *a, **kw: calls.append(("prefill", a, kw)) or "prefill",
    )
    run(load(path, {"UQ_FAST": "1"}), "on", calls)
    run(load(path, {"UQ_FAST": "0"}), "off", calls)
    # The shared buffer is required: a moved symbol must fail the boot, not skip.
    try:
        load(path, {"UQ_FAST": "1"})._patch_uq(types.SimpleNamespace())
    except RuntimeError:
        pass
    else:
        raise AssertionError("_patch_uq skipped a missing UltraQuantAttentionImpl")


if __name__ == "__main__":
    src = Path(next(p for p in ([sys.argv[1]] if len(sys.argv) > 1 else CANDIDATES) if p and Path(p).exists()))
    run_all(src)
    print(f"hook routing OK ({src})")
