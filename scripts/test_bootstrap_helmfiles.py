"""Bootstrap helmfiles read each chart from its Flux source, so a renamed app directory breaks bootstrap."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BootstrapHelmfileTests(unittest.TestCase):
    def test_every_release_resolves_its_chart(self):
        for helmfile in sorted((ROOT / "bootstrap/helmfile.d").glob("*.yaml")):
            with self.subTest(helmfile=helmfile.name):
                # build renders the release templates without pulling charts or touching a cluster.
                result = subprocess.run(["helmfile", "--file", str(helmfile), "build"], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
