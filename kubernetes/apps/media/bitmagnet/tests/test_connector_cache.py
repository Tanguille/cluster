"""Offline test of the prediction cache wrapper."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("connector", Path(__file__).resolve().parents[1] / "app/config/connector.py")
connector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connector)


def row(h, seeders=5, name="n", files=1):
    return {"infohash": h, "name": name, "files": [{}] * files, "swarm": {"tracker_seeders": seeders}}


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.now = [], 0.0
        self.enterContext(patch.object(connector.time, "monotonic", lambda: self.now))
        run = lambda _self, rows: self.calls.append([r["infohash"] for r in rows]) or [f"p-{r['infohash']}" for r in rows]
        self.cached = connector.cached(run)

    def test_hits_skip_model_and_keep_order(self):
        self.assertEqual(["p-a", "p-b"], self.cached(None, [row("a"), row("b")]))
        self.assertEqual(["p-b", "p-c", "p-a"], self.cached(None, [row("B"), row("c"), row("a")]))
        self.assertEqual([["a", "b"], ["c"]], self.calls)

    def test_changed_inputs_reclassify(self):
        self.cached(None, [row("a", seeders=0)])
        self.cached(None, [row("a", seeders=1)])  # dead -> alive crosses a bucket
        self.cached(None, [row("a", seeders=2)])  # same bucket: hit
        self.cached(None, [row("a", seeders=2, files=3)])
        self.assertEqual([["a"], ["a"], ["a"]], self.calls)

    def test_entries_expire(self):
        self.cached(None, [row("a")])
        self.now = connector.TTL + 1
        self.cached(None, [row("a")])
        self.assertEqual([["a"], ["a"]], self.calls)

    def test_rows_without_infohash_are_never_cached(self):
        self.cached(None, [row("")])
        self.cached(None, [row("")])
        self.assertEqual(2, len(self.calls))


if __name__ == "__main__":
    unittest.main()
