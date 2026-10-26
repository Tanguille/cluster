#!/usr/bin/env python3
"""Regression checks for the inline OCI chart Renovate regex manager."""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".renovaterc.json5"
MANAGER_DESCRIPTIONS = (
    'description: "Inline OCI chart specs with URL before ref"',
    'description: "Inline OCI chart specs with ref before URL"',
)


def read_manager():
    config = CONFIG.read_text()
    patterns = []
    for description in MANAGER_DESCRIPTIONS:
        start = config.index(description)
        end = config.index("\n    }", start)
        manager = config[start:end]
        strings = re.search(r"matchStrings:\s*(\[[\s\S]*?\n\s*\])", manager)
        if not strings:
            raise AssertionError(f"{description} has no matchStrings")
        # matchStrings is a JSON-compatible array of quoted strings in JSON5.
        matches = json.loads(strings.group(1))
        if len(matches) != 1:
            raise AssertionError(f"expected one match per manager, got {len(matches)}")
        # Python spells named captures differently from Renovate's RE2 syntax.
        pattern = re.sub(r"\(\?<([A-Za-z][A-Za-z0-9_]*)>", r"(?P<\1>", matches[0])
        patterns.append(re.compile(pattern))
    return patterns


class InlineOciRenovateRegexTest(unittest.TestCase):
    def test_both_field_orders_and_quoted_tags_match_once(self):
        patterns = read_manager()
        fixtures = [
            (
                """spec:
  chart:
    kind: OCIRepository
    spec:
      ref:
        tag: '1.2.3'
      url: oci://ghcr.io/example/charts/demo
  values:
    image:
      repository: ghcr.io/example/app
      tag: 9.8.7
""",
                "ghcr.io/example/charts/demo",
                "1.2.3",
            ),
            (
                """spec:
  chart:
    kind: OCIRepository
    spec:
      url: oci://quay.io/example/charts/other
      ref:
        tag: 2.3.4
""",
                "quay.io/example/charts/other",
                "2.3.4",
            ),
        ]
        for content, expected_name, expected_tag in fixtures:
            with self.subTest(expected_name=expected_name):
                matches = [list(pattern.finditer(content)) for pattern in patterns]
                self.assertEqual(sum(map(len, matches)), 1)
                match = next(result[0] for result in matches if result)
                self.assertEqual(match.group("depName"), expected_name)
                self.assertEqual(match.group("currentValue"), expected_tag)
                self.assertEqual(match.group(0).count(expected_tag), 1)
                updated = (
                    content[: match.start()]
                    + match.group(0).replace(expected_tag, "9.9.9", 1)
                    + content[match.end() :]
                )
                self.assertIn(f"url: oci://{expected_name}", updated)
                self.assertRegex(updated, r"tag: ['\"]?9\.9\.9")
                if "repository: ghcr.io/example/app" in content:
                    self.assertIn("tag: 9.8.7", updated)

    def test_values_only_image_is_not_an_oci_chart(self):
        content = """spec:
  values:
    image:
      repository: ghcr.io/example/app
      tag: 1.2.3
        """
        self.assertEqual([p.search(content) for p in read_manager()], [None, None])

    def test_chart_without_ref_does_not_borrow_values_ref_tag(self):
        content = """spec:
  chart:
    kind: OCIRepository
    spec:
      url: oci://ghcr.io/example/charts/no-ref
  values:
    ref:
      tag: 9.8.7
"""
        self.assertEqual([p.search(content) for p in read_manager()], [None, None])

    def test_migrated_helmreleases_extract_one_url_and_tag(self):
        patterns = read_manager()
        files = list((ROOT / "kubernetes/apps").rglob("helmrelease.yaml"))
        extracted = 0
        for path in files:
            content = path.read_text()
            if not re.search(
                r"(?m)^  chart:\s*\n    kind:\s*OCIRepository\s*\n    spec:",
                content,
            ):
                continue
            matches = [list(pattern.finditer(content)) for pattern in patterns]
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual(sum(map(len, matches)), 1)
                match = next(result[0] for result in matches if result)
                self.assertTrue(match.group("depName"))
                self.assertTrue(match.group("currentValue"))
            extracted += 1
        self.assertGreater(extracted, 0, "no inline OCI HelmRelease fixtures found")


if __name__ == "__main__":
    unittest.main()
