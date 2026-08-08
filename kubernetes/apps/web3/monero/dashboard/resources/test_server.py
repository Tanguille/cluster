#!/usr/bin/env python3
"""Checks for the two-tier retention added for the long-range hashrate chart.

Run from this directory: python3 -m unittest test_server
"""

import os
import tempfile
import time
import unittest

os.chdir(tempfile.mkdtemp())  # server.py mkdirs its --data-dir at import
import server  # noqa: E402


class RollupTest(unittest.TestCase):
    def setUp(self):
        server.log = server.new_series()
        server.rollup = server.new_series()
        server._bucket.update(key=None, count=0, **{k: 0.0 for k in server.SERIES})

    def feed(self, start, count, step=10, my_hash=lambda i: 100.0):
        for i in range(count):
            server.accumulate_rollup(start + i * step, {
                "myHash": my_hash(i), "poolHash": 0.0, "netHash": 0.0, "price": 0.0
            })

    def test_bucket_emits_the_mean_only_once_the_next_bucket_opens(self):
        # 30 samples at 10s exactly fill one 300s bucket; nothing is emitted until
        # sample 31 lands in the next one, so a half-filled bucket never charts.
        self.feed(0, 30, my_hash=lambda i: float(i))
        self.assertEqual(len(server.rollup["timestamps"]), 0)

        self.feed(300, 1)
        self.assertEqual(list(server.rollup["timestamps"]), [0])
        self.assertAlmostEqual(server.rollup["myHash"][0], 14.5)  # mean of 0..29

    def test_rollup_evicts_past_the_retention_horizon(self):
        # One sample per bucket, spanning a day more than ROLLUP_MAX_AGE.
        buckets = server.ROLLUP_MAX_AGE // server.ROLLUP_INTERVAL + 288
        self.feed(0, buckets + 1, step=server.ROLLUP_INTERVAL)

        horizon = server.ROLLUP_MAX_AGE // server.ROLLUP_INTERVAL
        self.assertLessEqual(len(server.rollup["timestamps"]), horizon)
        newest = server.rollup["timestamps"][-1]
        self.assertGreaterEqual(server.rollup["timestamps"][0], newest - server.ROLLUP_MAX_AGE)


class DownsampleTest(unittest.TestCase):
    def build(self, count, step, now):
        source = server.new_series()
        for i in range(count):
            source["timestamps"].append(now - (count - 1 - i) * step)
            for k in server.SERIES:
                source[k].append(float(i))
        return source

    def test_a_full_day_of_10s_samples_is_capped_at_the_point_budget(self):
        now = time.time()  # downsample() windows against the real clock
        source = self.build(8640, 10, now)  # 24h at the logger's cadence

        out = server.downsample(source, 24, max_points=server.CHART_MAX_POINTS)

        self.assertLessEqual(len(out["timestamps"]), server.CHART_MAX_POINTS)
        self.assertEqual(len(out["myHash"]), len(out["timestamps"]))
        # 8640/720 = 12 samples per bucket; the first averages values 0..11.
        self.assertAlmostEqual(out["myHash"][0], 5.5)

    def test_samples_older_than_the_window_are_dropped(self):
        now = time.time()
        source = self.build(720, 300, now)  # 60h, asked for the last 6

        out = server.downsample(source, 6)

        self.assertTrue(out["timestamps"])
        self.assertGreaterEqual(min(out["timestamps"]), now - 6 * 3600 - 300)

    def test_an_empty_source_returns_empty_columns_rather_than_raising(self):
        out = server.downsample(server.new_series(), 24)

        self.assertEqual(out["timestamps"], [])
        self.assertEqual(out["myHash"], [])


if __name__ == "__main__":
    unittest.main()
