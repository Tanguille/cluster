import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "kubernetes/apps/default/family-blog/app/config/repair-month-links.py"


class MonthLinkRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "site"
        self.root.mkdir()
        (self.root / "months").mkdir()
        (self.root / "months" / "01-2024.html").write_bytes(b"month")
        self.index = b'<nav><a href="index.html">Home</a><a href="index.html">01-2024</a><a href="https://example.test">02-2024</a></nav>\n'
        (self.root / "index.html").write_bytes(self.index)
        (self.root / "posts").mkdir()
        (self.root / "posts" / "post.html").write_bytes(b'<a href="../index.html"><b> 01-2024 </b></a><img src="index.html">\n')
        (self.root / "months" / "02-2024.html").write_bytes(b'<a href="../index.html">01-2024</a><a href="../index.html">02-2024</a>')
        self.marker = self.root.parent / ".ready"
        self.write_marker()

    def tearDown(self):
        self.tmp.cleanup()

    def write_marker(self):
        files = [p for p in self.root.rglob("*") if p.is_file()]
        self.marker.write_text(json.dumps({"files": len(files), "bytes": sum(p.stat().st_size for p in files)}))

    def run_script(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--site", str(self.root), *args], capture_output=True, text=True)

    def test_repairs_only_month_label_anchor_href_preserving_other_bytes(self):
        before = self.index
        result = self.run_script("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "index.html").read_bytes(), before.replace(b'href="index.html">01-2024', b'href="months/01-2024.html">01-2024'))
        self.assertIn(b'href="index.html">Home', (self.root / "index.html").read_bytes())
        self.assertEqual((self.root / "posts" / "post.html").read_bytes(), b'<a href="../months/01-2024.html"><b> 01-2024 </b></a><img src="index.html">\n')
        marker = json.loads(self.marker.read_text())
        self.assertEqual(marker["files"], 4)
        self.assertEqual(marker["bytes"], sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()))

    def test_dry_run_does_not_change_files_or_marker(self):
        old_marker = self.marker.read_bytes()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "index.html").read_bytes(), self.index)
        self.assertEqual(self.marker.read_bytes(), old_marker)

    def test_idempotent_and_corrects_marker_even_with_no_link_changes(self):
        self.assertEqual(self.run_script("--apply").returncode, 0)
        self.marker.write_text('{"files": 0, "bytes": 0}')
        self.assertEqual(self.run_script("--apply").returncode, 0)
        data = json.loads(self.marker.read_text())
        self.assertEqual(data["files"], 4)
        self.assertEqual(data["bytes"], sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()))

    def test_missing_target_preflight_leaves_site_unchanged(self):
        (self.root / "index.html").write_bytes(b'<a href="index.html">01-2024</a><a href="index.html">03-2024</a>')
        self.write_marker()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        old_marker = self.marker.read_bytes()
        result = self.run_script("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(self.marker.read_bytes(), old_marker)

    def test_refuses_symlink_escape(self):
        outside = Path(self.tmp.name) / "outside.html"
        outside.write_text("x")
        (self.root / "months" / "02-2024.html").unlink()
        (self.root / "months" / "02-2024.html").symlink_to(outside)
        (self.root / "index.html").write_bytes(b'<a href="index.html">02-2024</a>')
        self.assertNotEqual(self.run_script("--apply").returncode, 0)

    def test_refuses_missing_marker_independently(self):
        self.marker.unlink()
        self.assertNotEqual(self.run_script("--apply").returncode, 0)

    def test_attribute_names_are_tokenized_and_ambiguous_href_is_ignored(self):
        path = self.root / "index.html"
        path.write_bytes(b'<a title="href=\'index.html\'" data-href="index.html">01-2024</a><a href="index.html" href="index.html">01-2024</a>')
        self.assertEqual(self.run_script("--apply").returncode, 0)
        self.assertEqual(path.read_bytes(), b'<a title="href=\'index.html\'" data-href="index.html">01-2024</a><a href="index.html" href="index.html">01-2024</a>')

    def test_only_href_resolving_to_home_index_is_repaired(self):
        path = self.root / "posts" / "post.html"
        original = b'<a href="../index.html">01-2024</a><a href="index.html">01-2024</a><a href="../../index.html">01-2024</a>'
        path.write_bytes(original)
        self.write_marker()
        self.assertEqual(self.run_script("--apply").returncode, 0)
        self.assertEqual(path.read_bytes(), b'<a href="../months/01-2024.html">01-2024</a><a href="index.html">01-2024</a><a href="../../index.html">01-2024</a>')

    def test_single_quote_non_ascii_and_already_correct_bytes(self):
        path = self.root / "index.html"
        correct = b'<a href="months/01-2024.html">01-2024</a>'
        path.write_bytes('é<a href=\'index.html\'>01-2024</a>'.encode("utf-8") + correct)
        self.write_marker()
        self.assertEqual(self.run_script("--apply").returncode, 0)
        expected = 'é<a href=\'months/01-2024.html\'>01-2024</a>'.encode("utf-8") + correct
        self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(self.run_script("--apply").returncode, 0)
        self.assertEqual(path.read_bytes(), expected)

    def test_invalid_ready_json_refuses_without_site_or_marker_changes(self):
        for invalid in ("[]", "null", '{"files": true, "bytes": 0}', '{"files": 1, "bytes": -1}'):
            with self.subTest(invalid=invalid):
                self.marker.write_text(invalid)
                before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
                marker_before = self.marker.read_bytes()
                self.assertNotEqual(self.run_script("--apply").returncode, 0)
                self.assertEqual({p: p.read_bytes() for p in before}, before)
                self.assertEqual(self.marker.read_bytes(), marker_before)

    def test_valid_marker_extra_fields_are_preserved(self):
        self.marker.write_text('{"files": 5, "bytes": 1, "other": "keep"}')
        self.assertEqual(self.run_script("--apply").returncode, 0)
        self.assertEqual(json.loads(self.marker.read_text())["other"], "keep")


if __name__ == "__main__":
    unittest.main()
