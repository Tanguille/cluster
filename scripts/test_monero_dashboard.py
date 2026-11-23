import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "kubernetes/apps/web3/monero/dashboard/resources"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import server


class DashboardTest(unittest.TestCase):
    def test_fx_failure_preserves_eur_price(self):
        with patch.dict(server._price_cache, ts=0), patch.dict(server.log, price=[150]), patch.object(
            server, "_fetch_json", side_effect=[OSError(), OSError(), [0] * 6 + [200], OSError()]
        ):
            self.assertEqual(server.get_xmr_price(), 150)

    def test_sigterm_saves_before_waiting_for_http_threads(self):
        events = []
        http = Mock()
        http.serve_forever.side_effect = lambda: server._sigterm()
        with patch.object(server, "Server") as factory, patch.object(server.signal, "signal"), patch.object(
            server, "load_log_disk"
        ), patch.object(server.threading, "Thread"), patch.object(server, "shutdown_event") as shutdown, patch.object(
            server, "save_log_disk", side_effect=lambda: events.append("save")
        ):
            shutdown.set.side_effect = lambda: events.append("stop")
            factory.return_value.__enter__.return_value = http
            factory.return_value.__exit__.side_effect = lambda *_: events.append("close")
            server.main()
        self.assertEqual(events, ["stop", "save", "close"])
