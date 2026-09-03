# Lifts vLLM's refusal to pair ROCM_AITER_UNIFIED_ATTN with a KV connector.
#
# backend.py gates on `use_kv_connector and not cls.supports_kv_connector()`.
# RocmAiterUnifiedAttentionBackend inherits that method as False from
# RocmAttentionBackend, whose in-code reason is its own (2, num_blocks, ...)
# packing. That reason does not apply to the subclass: it overrides
# customize_spec and advertises LBHNC, which is exactly what
# OffloadingConnector.get_required_kvcache_layout() returns. The inherited False
# looks like a missing override rather than a deliberate exclusion.
#
# Patched via an import hook rather than by overlaying the upstream source file,
# so this survives the frequent Renovate digest bumps of the nightly image
# instead of silently going stale against a changed file.
#
# If upstream ever adds its own override, or renames the module/class, this hook
# simply never fires and the engine falls back to TRITON_ATTN. Confirm which
# backend is live from the startup log rather than assuming this took effect.
import importlib.abc
import sys

TARGET = "vllm.v1.attention.backends.rocm_aiter_unified_attn"


class _PatchLoader(importlib.abc.Loader):
    # Hold the ORIGINAL loader, not the spec: find_spec() overwrites spec.loader
    # with this wrapper, so going back through the spec recurses into itself.
    def __init__(self, loader):
        self._loader = loader

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        module.RocmAiterUnifiedAttentionBackend.supports_kv_connector = classmethod(
            lambda cls: True
        )
        print("[aiter-kvconn-patch] supports_kv_connector -> True",
              file=sys.stderr, flush=True)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != TARGET:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            spec = finder.find_spec(name, path, target)
            if spec and spec.loader:
                spec.loader = _PatchLoader(spec.loader)
                return spec
        return None


sys.meta_path.insert(0, _Finder())
print("[aiter-kvconn-patch] import hook installed", file=sys.stderr, flush=True)
