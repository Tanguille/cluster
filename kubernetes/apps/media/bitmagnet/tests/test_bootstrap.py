"""Offline: no installs, downloads or real PVCs."""
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "app/config/bootstrap.py"
CONTENT = b"# connector fixture\n"


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "data"
        self.root.mkdir()
        self.requirements = Path(temp.name) / "requirements.txt"
        self.requirements.write_text("numpy==1\n")
        self.enterContext(patch.dict(os.environ, {"ARMIGER_ROOT": str(self.root),
                                                  "ARMIGER_REQUIREMENTS": str(self.requirements)}))
        self.code = compile(SCRIPT.read_text(), str(SCRIPT), "exec")
        self.calls = []

    def fake_run(self, args, check):
        self.assertTrue(check)
        self.calls.append(args)
        if "snapshot_download" in args[-1]:
            (self.root / "model").mkdir()

    def execute(self, runner=None, code=None):
        with patch("subprocess.run", side_effect=runner or self.fake_run), \
             patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(CONTENT)) as fetch:
            exec(code or self.code, {"__name__": "__main__"})
        return fetch.call_count

    def test_success_is_idempotent(self):
        self.execute()
        first = len(self.calls)
        self.execute()
        self.assertEqual(first, len(self.calls))

    def test_requirements_change_reinstalls(self):
        self.execute()
        self.requirements.write_text("numpy==2\n")
        self.calls.clear()
        self.execute()
        self.assertTrue(any("-r" in call for call in self.calls))
        self.assertFalse(any("snapshot_download" in call[-1] for call in self.calls))

    def test_failed_install_does_not_mark_runtime_ready(self):
        def fail(args, check):
            raise subprocess.CalledProcessError(1, args)
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute(runner=fail)
        self.assertFalse((self.root / "runtime/.ready").exists())

    def test_connector_refetched_only_on_revision_change(self):
        self.assertEqual(1, self.execute())
        self.assertEqual(0, self.execute())
        bumped = compile(SCRIPT.read_text().replace('CONNECTOR_REVISION = "', 'CONNECTOR_REVISION = "0'),
                         str(SCRIPT), "exec")
        self.assertEqual(1, self.execute(code=bumped))
        self.assertEqual(CONTENT, (self.root / "runtime/bitmagnet_connector.py").read_bytes())

    def test_failed_model_download_retries_without_reinstall(self):
        def fail_model(args, check):
            if "snapshot_download" in args[-1]:
                raise subprocess.CalledProcessError(1, args)
            self.fake_run(args, check)
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute(runner=fail_model)
        self.calls.clear()
        self.execute()
        self.assertEqual(1, len(self.calls))
        self.assertIn("snapshot_download", self.calls[0][-1])


if __name__ == "__main__":
    unittest.main()
