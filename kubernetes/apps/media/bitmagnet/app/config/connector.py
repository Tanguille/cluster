"""Run the pinned upstream connector with a per-infohash prediction cache.

The Torznab proxy re-classifies every result on every search, and Prowlarr's RSS sync keeps
re-fetching the same newest items. Predictions also feed swarm viability, so entries expire.
"""
import importlib.util
import os
import time

TTL = float(os.environ.get("ARMIGER_PREDICTION_CACHE_SECONDS", "3600"))
MAX_ENTRIES = 50_000


def cached(run, ttl=TTL, clock=time.monotonic):
    """Wrap `run(self, rows) -> preds` so rows whose infohash was seen within `ttl` skip the model."""
    cache = {}

    def wrapper(self, rows):
        now = clock()
        out = [None] * len(rows)
        misses = []
        for i, row in enumerate(rows):
            key = str(row.get("infohash") or "").lower()
            hit = cache.get(key) if key else None
            if hit and now - hit[0] < ttl:
                out[i] = hit[1]
            else:
                misses.append(i)
        if misses:
            for i, pred in zip(misses, run(self, [rows[i] for i in misses])):
                out[i] = pred
                key = str(rows[i].get("infohash") or "").lower()
                if key:
                    cache[key] = (now, pred)
            if len(cache) > MAX_ENTRIES:
                for key in [k for k, (t, _) in cache.items() if now - t >= ttl] or list(cache)[: len(cache) // 2]:
                    del cache[key]
        return out

    return wrapper


if __name__ == "__main__":
    spec = importlib.util.spec_from_file_location("bitmagnet_connector", "/data/armiger/runtime/bitmagnet_connector.py")
    connector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(connector)
    # Fails loudly at startup if upstream renames the method.
    cls = connector.BitmagnetConnector
    cls._run_batch_inference = cached(cls._run_batch_inference)
    connector.main()
