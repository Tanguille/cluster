"""Offline test of the prediction cache wrapper."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("connector", Path(__file__).resolve().parents[1] / "app/config/connector.py")
connector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connector)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.now = [], 0.0
        run = lambda _self, rows: self.calls.append([r["infohash"] for r in rows]) or [f"p-{r['infohash']}" for r in rows]
        self.cached = connector.cached(run, ttl=10, clock=lambda: self.now)

    def test_hits_skip_model_and_keep_order(self):
        self.assertEqual(["p-a", "p-b"], self.cached(None, [{"infohash": "a"}, {"infohash": "b"}]))
        self.assertEqual(["p-b", "p-c", "p-a"], self.cached(None, [{"infohash": "B"}, {"infohash": "c"}, {"infohash": "a"}]))
        self.assertEqual([["a", "b"], ["c"]], self.calls)

    def test_entries_expire(self):
        self.cached(None, [{"infohash": "a"}])
        self.now = 11
        self.cached(None, [{"infohash": "a"}])
        self.assertEqual([["a"], ["a"]], self.calls)

    def test_rows_without_infohash_are_never_cached(self):
        self.cached(None, [{"infohash": ""}])
        self.cached(None, [{"infohash": ""}])
        self.assertEqual(2, len(self.calls))


if __name__ == "__main__":
    unittest.main()
