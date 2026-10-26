"""Every inline OCI chart must match the Renovate regex manager, or its tag is never bumped."""

import json
import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InlineOciRenovateTests(unittest.TestCase):
    def test_every_inline_chart_matches_once(self):
        config = (ROOT / ".renovaterc.json5").read_text()
        raw = re.search(r'^\s*("\\\\n    kind: OCIRepository.*")$', config, re.M).group(1)
        # Python spells RE2's (?<name>) named groups as (?P<name>).
        pattern = re.compile(json.loads(raw).replace("(?<", "(?P<"))
        charts = 0
        for path in (ROOT / "kubernetes/apps").rglob("helmrelease.yaml"):
            content = path.read_text()
            if "\n  chart:\n    kind: OCIRepository\n" not in content:
                continue
            charts += 1
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertEqual(len(pattern.findall(content)), 1)
        self.assertGreater(charts, 0)


if __name__ == "__main__":
    unittest.main()
