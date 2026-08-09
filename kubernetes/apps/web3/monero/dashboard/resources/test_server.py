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

    def test_an_idle_miners_null_hashrate_does_not_break_either_tier(self):
        # xmrig reports hashrate.total[0] as null while idle; it used to crash
        # accumulate_rollup on startup replay and downsample on every request.
        server.append_log(None, 1.0, 1.0, 1.0)
        self.feed(int(time.time()) + 600, 1)

        self.assertEqual(list(server.log["myHash"]), [0.0])
        self.assertTrue(server.downsample(server.log, 1)["myHash"])


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


class BackfillPriceTest(unittest.TestCase):
    def setUp(self):
        server.rollup = server.new_series()
        server._bucket.update(key=None, count=0, **{k: 0.0 for k in server.SERIES})

    def seed(self, prices):
        server.backfill_price_history.__globals__["_fetch_json"] = (
            lambda *a, **k: {"prices": prices}
        )

    def tearDown(self):
        server.backfill_price_history.__globals__["_fetch_json"] = server._fetch_json

    def test_price_only_buckets_carry_no_invented_hashrate(self):
        self.seed([[0, 300.0], [3_600_000, 310.0]])

        server.backfill_price_history()

        self.assertEqual(list(server.rollup["price"]), [300.0, 310.0])
        self.assertEqual(list(server.rollup["myHash"]), [None, None])
        # A window over those buckets must report no hashrate, not a flat zero
        out = server.downsample(server.rollup, 24 * 90)
        self.assertTrue(all(v is None for v in out["myHash"]))

    def test_a_logged_bucket_is_never_overwritten(self):
        server.accumulate_rollup(0, {k: 5.0 for k in server.SERIES})
        server.accumulate_rollup(server.ROLLUP_INTERVAL, {k: 5.0 for k in server.SERIES})
        self.seed([[0, 999.0]])

        server.backfill_price_history()

        self.assertEqual(list(server.rollup["price"]), [5.0])


class BreakGapsTest(unittest.TestCase):
    def window(self, stamps):
        return {"timestamps": list(stamps), **{k: [1.0] * len(stamps) for k in server.SERIES}}

    def test_a_stall_becomes_a_null_the_chart_can_break_on(self):
        # Steady 10s cadence, then an hour with no logger, then steady again.
        stamps = [0, 10, 20, 30, 3630, 3640, 3650]

        out = server.break_gaps(self.window(stamps))

        self.assertEqual(out["myHash"].count(None), 1)
        gap_at = out["myHash"].index(None)
        self.assertTrue(30 < out["timestamps"][gap_at] < 3630)

    def test_an_evenly_sampled_window_is_left_alone(self):
        out = server.break_gaps(self.window(range(0, 100, 10)))

        self.assertNotIn(None, out["myHash"])
        self.assertEqual(len(out["timestamps"]), 10)


if __name__ == "__main__":
    unittest.main()
