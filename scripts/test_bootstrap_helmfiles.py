"""Bootstrap helmfiles read each chart from its Flux source, so a renamed app directory breaks bootstrap."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BootstrapHelmfileTests(unittest.TestCase):
    def test_every_release_resolves_its_chart(self):
        # build renders every helmfile's release templates without pulling charts or touching a cluster.
        result = subprocess.run(["helmfile", "--file", str(ROOT / "bootstrap/helmfile.d"), "build"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
