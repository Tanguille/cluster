"""Run the pinned upstream connector with a prediction cache.

The Torznab proxy re-classifies every result on every search, and Prowlarr's RSS sync keeps
re-fetching the same newest items. The key holds every input that moves the prediction (name,
file count, seeder bucket), so a changed torrent or swarm is re-classified and new ones always are.
"""
from collections import OrderedDict
import importlib.util
import threading
import time

TTL = 24 * 3600
MAX_ENTRIES = 10_000


def _seeder_bucket(row):
    seeders = int((row.get("swarm") or {}).get("tracker_seeders") or 0)
    return next(i for i, cap in enumerate((0, 2, 9, 49, float("inf"))) if seeders <= cap)


def _key(row):
    infohash = str(row.get("infohash") or "").lower()
    if not infohash:
        return None
    return infohash, row.get("name"), len(row.get("files") or ()), _seeder_bucket(row)


def cached(run):
    """Wrap `run(self, rows) -> preds` with a thread-safe LRU of predictions."""
    cache = OrderedDict()
    lock = threading.Lock()

    def wrapper(self, rows):
        now = time.monotonic()
        keys = [_key(row) for row in rows]
        out = [None] * len(rows)
        with lock:
            for i, key in enumerate(keys):
                hit = cache.get(key) if key else None
                if hit and now - hit[0] < TTL:
                    cache.move_to_end(key)
                    out[i] = hit[1]
        misses = [i for i, pred in enumerate(out) if pred is None]
        if misses:
            preds = run(self, [rows[i] for i in misses])
            assert len(preds) == len(misses), "upstream changed the batch inference contract"
            with lock:
                for i, pred in zip(misses, preds):
                    out[i] = pred
                    if keys[i]:
                        cache[keys[i]] = (now, pred)
                        cache.move_to_end(keys[i])
                while len(cache) > MAX_ENTRIES:
                    cache.popitem(last=False)
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
