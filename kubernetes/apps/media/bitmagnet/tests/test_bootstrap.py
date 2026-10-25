"""Offline: no installs, downloads or real PVCs."""
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "app/bootstrap.py"
CONTENT = b"# verified connector fixture\n"


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
        source = re.sub(r'SOURCE_SHA256 = "\w+"', f'SOURCE_SHA256 = "{hashlib.sha256(CONTENT).hexdigest()}"',
                        SCRIPT.read_text())
        self.code = compile(source, str(SCRIPT), "exec")
        self.calls = []

    def fake_run(self, args, check):
        self.assertTrue(check)
        self.calls.append(args)
        if "snapshot_download" in args[-1]:
            (self.root / "model").mkdir()

    def execute(self, runner=None, content=CONTENT):
        with patch("subprocess.run", side_effect=runner or self.fake_run), \
             patch("urllib.request.urlopen", return_value=io.BytesIO(content)):
            exec(self.code, {"__name__": "__main__"})

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

    def test_legacy_layout_is_removed(self):
        (self.root / "runtime-v2/venv").mkdir(parents=True)
        (self.root / "model-old").mkdir()
        self.execute()
        self.assertEqual(["model", "runtime"], sorted(p.name for p in self.root.iterdir()))

    def test_failed_install_does_not_mark_runtime_ready(self):
        def fail(args, check):
            raise subprocess.CalledProcessError(1, args)
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute(runner=fail)
        self.assertFalse((self.root / "runtime/.ready").exists())

    def test_bad_source_digest_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
            self.execute(content=b"unexpected source")
        self.assertFalse((self.root / "runtime/bitmagnet_connector.py").exists())
        self.assertFalse((self.root / "model").exists())

    def test_modified_source_is_repaired_even_when_ready(self):
        self.execute()
        source = self.root / "runtime/bitmagnet_connector.py"
        source.write_bytes(b"corrupted")
        self.execute()
        self.assertEqual(CONTENT, source.read_bytes())

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
