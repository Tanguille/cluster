import sys
import unittest
from io import BytesIO
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "docs/llm-hosting/bench"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import _metrics
    import prodshape


class MetricsTest(unittest.TestCase):
    def test_idle_requires_two_complete_scrapes(self):
        running = b'vllm:num_requests_running{} 0\n'
        waiting = b'vllm:num_requests_waiting{} 0\n'
        idle = running + waiting
        busy = idle + b'vllm:num_requests_running{engine="2"} 1\n'
        for first, last, expected in [
            (b'', b'', False), (running, running, False), (waiting, waiting, False),
            (idle, busy, False), (busy, idle, False), (idle, idle, True),
            (idle, idle.replace(b'0', b'NaN'), False),
        ]:
            with self.subTest(first=first, last=last), patch.object(
                _metrics.urllib.request, 'urlopen', side_effect=map(BytesIO, (first, last))
            ), patch.object(_metrics.time, 'sleep'):
                self.assertEqual(_metrics.wait_idle(0, tries=1).idle, expected)

    def test_contamination_preserves_missing_data(self):
        for checks, expected in [
            ([], None), ([False, None], None), ([True, None], True), ([False], False),
        ]:
            self.assertIs(prodshape.any_contaminated(checks), expected)

    def test_queue_published_before_vram_command(self):
        poller = prodshape.Poller(0, 'mock-vram')
        observed = []

        def read_vram(*args, **kwargs):
            observed.extend(poller.rows[-1])
            poller.stop.set()
            return SimpleNamespace(stdout='100')

        with patch.object(prodshape, 'sample', side_effect=[
            _metrics.Sample(i, 0) for i in range(5)
        ]), patch.object(poller.stop, 'wait'), patch.object(prodshape.subprocess, 'run', read_vram):
            poller.run()
        self.assertEqual(observed[1:], [4, 0, None])
        self.assertEqual(poller.rows[-1][3], 100)

    def test_level_only_includes_observations_inside_its_window(self):
        poller = prodshape.Poller(0, None)
        poller.rows = [[0, 3, 7, None], [10, 1, 8, None],
                       [20, 1, 2, None], [31, 3, 9, None]]
        args = SimpleNamespace(seed=1, turns=1, port=0)
        with patch.object(prodshape.time, 'perf_counter', side_effect=[10, 20, 30]), patch.object(
            _metrics.urllib.request, 'urlopen', side_effect=[BytesIO(), BytesIO()]
        ):
            result = prodshape.run_level(args, '', 0, 1, [], poller, poller.stop)
        self.assertIs(result['contaminated'], False)
        self.assertEqual(result['max_waiting'], 2)
