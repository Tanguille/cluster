"""Offline tests: never install packages, download models, or touch real PVCs."""
import ast
import hashlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "app/bootstrap.py"
CONTENT = b"# verified connector fixture\n"


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tree = ast.parse(SCRIPT.read_text())
        # Replace only storage root and expected fixture hash; execute real logic.
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id == "ROOT":
                    node.value = ast.Call(func=ast.Name(id="Path", ctx=ast.Load()),
                                          args=[ast.Constant(str(self.root))], keywords=[])
                elif node.targets[0].id == "SOURCE_SHA256":
                    node.value = ast.Constant(hashlib.sha256(CONTENT).hexdigest())
        self.code = compile(ast.fix_missing_locations(tree), str(SCRIPT), "exec")
        self.calls = []

    def fake_run(self, args, check):
        self.assertTrue(check)
        self.calls.append(args)
        if "snapshot_download" in args[-1]:
            # Emulate the successful download, not its completion marker.
            (self.root / "model-cdf7036c45a0d9751d18e738c7abfde485552964").mkdir(exist_ok=True)

    def execute(self, runner=None, content=CONTENT):
        with patch("subprocess.run", side_effect=runner or self.fake_run), \
             patch("urllib.request.urlopen", return_value=io.BytesIO(content)):
            exec(self.code, {"__name__": "__main__"})

    def test_success_is_idempotent_and_preserves_old_runtime(self):
        old = self.root / "bitmagnet_connector.py"
        old.write_text("old runtime")
        self.execute()
        self.assertTrue((self.root / "runtime-v2/.ready").exists())
        first = len(self.calls)
        self.execute()
        self.assertEqual(first, len(self.calls))
        self.assertEqual("old runtime", old.read_text())

    def test_failed_install_does_not_mark_runtime_ready(self):
        def fail(args, check):
            raise subprocess.CalledProcessError(1, args)
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute(runner=fail)
        self.assertFalse((self.root / "runtime-v2/.ready").exists())

    def test_bad_source_digest_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
            self.execute(content=b"unexpected source")
        self.assertFalse((self.root / "runtime-v2/bitmagnet_connector.py").exists())
        self.assertFalse(list(self.root.glob("model-*/.ready")))

    def test_modified_source_is_repaired_even_when_ready(self):
        self.execute()
        source = self.root / "runtime-v2/bitmagnet_connector.py"
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
        self.assertFalse(list(self.root.glob("model-*/.ready")))
        self.calls.clear()
        self.execute()
        self.assertEqual(1, len(self.calls))
        self.assertIn("snapshot_download", self.calls[0][-1])


if __name__ == "__main__":
    unittest.main()
