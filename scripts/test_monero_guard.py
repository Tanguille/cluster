import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "kubernetes/apps/web3/monero/guard/resources"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import controller


class ThermalTripTest(unittest.TestCase):
    def test_repeat_peer_cannot_mask_panic_or_advance_recovery(self):
        node = "control-2"
        telemetry = Mock()
        clock = Mock()
        start = controller._dt(1000)
        sensors = controller.SENSORS[node]
        with patch.object(controller, "SENSORS", {node: sensors}):
            guard = controller.GuardController(telemetry, clock)

            def evaluate(seconds, temperature, peer_seconds):
                clock.return_value = seconds
                now = start + timedelta(seconds=seconds)
                peer_time = start + timedelta(seconds=peer_seconds)
                telemetry.query_nvme.return_value = [
                    controller.Source(temperature, now), controller.Source(50, peer_time)
                ]
                return guard.evaluate(now)[node]

            guard.policies[node].safe = True
            self.assertEqual(evaluate(0, 55, 0), 1)
            self.assertEqual(evaluate(30, 67, 0), 0)
            self.assertEqual(evaluate(60, 55, 0), 0)
            for seconds in (90, 150, 210):
                self.assertEqual(evaluate(seconds, 55, seconds), 0)
            self.assertEqual(evaluate(270, 55, 270), 1)
