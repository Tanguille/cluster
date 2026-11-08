"""Synthetic-only tests for the private family-blog archive importer.

No fixture here contains personal content; every byte is generated in this file.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import threading
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
IMPORTER_PATH = ROOT / "kubernetes/apps/default/family-blog/seed/import.py"

OFFLINE_FILES = {
    "offline/index.html": b"<!doctype html>\n<title>Family archive</title>\n",
    "offline/assets/site.css": b"body { color: #222; }\n",
    "offline/posts/hello.html": b"<h1>Synthetic post</h1>\n",
}
EXTRA_FILES = {
    "raw/capture.html": b"synthetic raw capture\n",
    "reports/report.txt": b"synthetic report\n",
}
FULL_ARCHIVE_FILES = {
    "README.md": b"# synthetic archive\n",
    "metadata/capture_summary.json": b'{"synthetic": true}\n',
    "metadata/checksums.json": b"{}\n",
    "offline/index.html": OFFLINE_FILES["offline/index.html"],
    "offline/assets/site.css": OFFLINE_FILES["offline/assets/site.css"],
    "offline/posts/hello.html": OFFLINE_FILES["offline/posts/hello.html"],
    "raw/capture.html": EXTRA_FILES["raw/capture.html"],
    "reports/report.txt": EXTRA_FILES["reports/report.txt"],
}
CHECKSUMS = "metadata/SHA256SUMS.txt"


def load_importer():
    spec = importlib.util.spec_from_file_location("family_blog_seed_import", IMPORTER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load importer from {IMPORTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sums_text(files):
    lines = []
    for name, data in files.items():
        lines.append(f"{sha(data)}  {name}")
    return "\n".join(lines) + "\n"


def manifest_text(files):
    return "".join(f"{sha(data)}  {name}\n" for name, data in files.items())


def member(name, data=b"", kind="file", mode=0o644):
    return (name, data, kind, mode)


def make_zip(members, *, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w", compression) as archive:
            for name, data, kind, mode in members:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.compress_type = compression
                if kind == "dir":
                    info.external_attr = (stat.S_IFDIR | 0o755) << 16
                    info.external_attr |= 0x10
                    payload = b""
                elif kind == "symlink":
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    payload = data
                elif kind == "fifo":
                    info.external_attr = (stat.S_IFIFO | 0o644) << 16
                    payload = b""
                elif kind == "char":
                    info.external_attr = (stat.S_IFCHR | 0o644) << 16
                    payload = b""
                else:
                    info.external_attr = (stat.S_IFREG | mode) << 16
                    payload = data
                archive.writestr(info, payload)
    return buffer.getvalue()


def valid_members(files=None, *, extras=None, sums=None, dirs=True, file_mode=0o644):
    files = OFFLINE_FILES if files is None else files
    extras = EXTRA_FILES if extras is None else extras
    members = []
    if dirs:
        for name in ("offline/", "metadata/", "raw/", "reports/"):
            members.append(member(name, kind="dir"))
    members.extend(member(name, data, mode=file_mode) for name, data in files.items())
    members.extend(member(name, data) for name, data in extras.items())
    if sums is not False:
        text = sums_text(files) if sums is None else sums
        members.append(member(CHECKSUMS, text.encode()))
    return members


def full_archive_members(manifest=None):
    files = FULL_ARCHIVE_FILES
    text = manifest_text(files) if manifest is None else manifest
    members = [
        member("offline/", kind="dir"),
        member("metadata/", kind="dir"),
        member("raw/", kind="dir"),
        member("reports/", kind="dir"),
    ]
    members.extend(member(name, data) for name, data in files.items())
    members.append(member(CHECKSUMS, text.encode()))
    return members


def snapshot(root: Path):
    result = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_dir():
            result[rel] = ("dir",)
        else:
            result[rel] = ("file", path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    return result


class FamilyBlogSeedTests(unittest.TestCase):
    def setUp(self):
        self.module = load_importer()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dest = self.fresh_destination()

    def fresh_destination(self) -> Path:
        dest = Path(tempfile.mkdtemp(dir=self.root)) / "data"
        dest.mkdir()
        (dest / "lost+found").mkdir()
        (dest / "lost+found" / "marker").write_bytes(b"filesystem-created")
        return dest

    def import_bytes(self, data, expected=None, destination=None, **kwargs):
        return self.module.import_archive(
            io.BytesIO(data),
            sha(data) if expected is None else expected,
            self.dest if destination is None else destination,
            **kwargs,
        )

    def assert_refused(self, data, expected=None, **kwargs):
        before = snapshot(self.dest)
        with self.assertRaises(ValueError):
            self.import_bytes(data, expected, **kwargs)
        self.assertFalse((self.dest / ".ready").exists())
        self.assertFalse((self.dest / "site").exists())
        self.assertEqual(snapshot(self.dest), before)

    # -- limits ---------------------------------------------------------
    def test_limits_match_brief_defaults(self):
        self.assertEqual(self.module.MAX_COMPRESSED_BYTES, 256 * 1024 * 1024)
        self.assertEqual(self.module.MAX_EXPANDED_BYTES, 512 * 1024 * 1024)
        self.assertEqual(self.module.MAX_FILES, 10_000)
        self.assertEqual(self.module.MAX_METADATA_BYTES, 8 * 1024 * 1024)

    # -- happy path -----------------------------------------------------
    def test_valid_import_extracts_only_offline_and_writes_ready(self):
        data = make_zip(valid_members())

        result = self.import_bytes(data)

        self.assertEqual(result["files"], len(OFFLINE_FILES))
        self.assertEqual(result["bytes"], sum(len(v) for v in OFFLINE_FILES.values()))
        self.assertEqual(
            {p.name for p in self.dest.iterdir()}, {"lost+found", "site", ".ready"}
        )
        site = self.dest / "site"
        extracted = {
            str(p.relative_to(site)) for p in site.rglob("*") if p.is_file()
        }
        self.assertEqual(
            extracted, {name[len("offline/") :] for name in OFFLINE_FILES}
        )
        for name, payload in OFFLINE_FILES.items():
            target = site / name[len("offline/") :]
            actual = target.read_bytes()
            self.assertEqual(actual, payload)
            self.assertEqual(sha(actual), sha(payload))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertFalse((site / "metadata").exists())
        self.assertFalse((site / "raw").exists())
        self.assertFalse((site / "reports").exists())
        self.assertFalse(list(self.dest.rglob("*.zip")))
        ready = json.loads((self.dest / ".ready").read_text())
        self.assertEqual(ready["files"], len(OFFLINE_FILES))
        self.assertEqual(ready["bytes"], sum(len(v) for v in OFFLINE_FILES.values()))
        self.assertEqual(stat.S_IMODE(site.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((site / "assets").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.dest / ".ready").stat().st_mode), 0o644)

    def test_valid_import_uses_archive_root_manifest_with_mixed_entries(self):
        # Mirrors the real archive layout: README, metadata, offline, raw and
        # reports entries all appear archive-root relative in the manifest.
        data = make_zip(full_archive_members())

        result = self.import_bytes(data)

        self.assertEqual(result["files"], len(OFFLINE_FILES))
        self.assertEqual(result["bytes"], sum(len(v) for v in OFFLINE_FILES.values()))
        site = self.dest / "site"
        extracted = {
            str(p.relative_to(site)) for p in site.rglob("*") if p.is_file()
        }
        self.assertEqual(
            extracted, {name[len("offline/") :] for name in OFFLINE_FILES}
        )
        self.assertFalse((site / "README.md").exists())
        self.assertFalse((site / "metadata").exists())
        self.assertFalse((site / "raw").exists())
        self.assertFalse((site / "reports").exists())

    def test_archive_permissions_are_not_trusted(self):
        data = make_zip(valid_members(file_mode=0o777))
        self.import_bytes(data)
        for name in OFFLINE_FILES:
            target = self.dest / "site" / name[len("offline/") :]
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    # -- index and checksum metadata -----------------------------------
    def test_missing_index_is_refused(self):
        files = {
            name: data
            for name, data in OFFLINE_FILES.items()
            if name != "offline/index.html"
        }
        self.assert_refused(make_zip(valid_members(files)))

    def test_empty_index_is_refused(self):
        files = dict(OFFLINE_FILES)
        files["offline/index.html"] = b""
        self.assert_refused(make_zip(valid_members(files)))

    def test_missing_checksum_metadata_is_refused(self):
        self.assert_refused(make_zip(valid_members(sums=False)))

    def test_wrong_archive_checksum_is_refused(self):
        self.assert_refused(make_zip(valid_members()), expected="0" * 64)

    def test_malformed_expected_checksum_is_refused(self):
        data = make_zip(valid_members())
        before = snapshot(self.dest)
        with self.assertRaises(ValueError):
            self.import_bytes(data, expected="not-a-sha256")
        self.assertEqual(snapshot(self.dest), before)

    def test_wrong_file_checksum_is_refused(self):
        sums = sums_text(OFFLINE_FILES)
        files = dict(OFFLINE_FILES)
        files["offline/assets/site.css"] = b"tampered after hashing\n"
        self.assert_refused(make_zip(valid_members(files, sums=sums)))

    def test_unlisted_offline_file_is_refused(self):
        files = dict(OFFLINE_FILES)
        files["offline/extra.html"] = b"not listed\n"
        self.assert_refused(make_zip(valid_members(files, sums=sums_text(OFFLINE_FILES))))

    def test_listed_but_missing_offline_file_is_refused(self):
        sums = sums_text(OFFLINE_FILES)
        sums += f"{sha(b'absent')}  offline/absent.html\n"
        self.assert_refused(make_zip(valid_members(sums=sums)))

    def test_duplicate_checksum_entry_is_refused(self):
        sums = sums_text(OFFLINE_FILES)
        sums += f"{sha(OFFLINE_FILES['offline/index.html'])}  offline/index.html\n"
        self.assert_refused(make_zip(valid_members(sums=sums)))

    def test_duplicate_nonoffline_checksum_entry_is_refused(self):
        manifest = manifest_text(FULL_ARCHIVE_FILES)
        manifest += f"{sha(FULL_ARCHIVE_FILES['README.md'])}  README.md\n"
        self.assert_refused(make_zip(full_archive_members(manifest)))

    def test_duplicate_zip_member_is_refused(self):
        members = valid_members()
        members.append(member("offline/index.html", OFFLINE_FILES["offline/index.html"]))
        self.assert_refused(make_zip(members))

    def test_truncated_zip_is_refused(self):
        data = make_zip(valid_members())
        cut = data[: max(10, len(data) // 2)]
        self.assert_refused(cut)

    # -- unsafe member names and types ----------------------------------
    def test_traversal_and_absolute_member_names_are_refused(self):
        for bad_name in (
            "../escape.html",
            "offline/../../escape.html",
            "/etc/passwd",
            "C:/windows/evil",
            "offline/..\\escape.html",
            "offline/./index.html",
            "offline//double.html",
        ):
            with self.subTest(name=bad_name):
                self.assert_refused(make_zip(valid_members() + [member(bad_name, b"x")]))

    def test_traversal_checksum_path_is_refused(self):
        sums = sums_text(OFFLINE_FILES)
        sums += f"{sha(b'x')}  ../../etc/passwd\n"
        self.assert_refused(make_zip(valid_members(sums=sums)))

    def test_nul_truncated_member_name_is_refused(self):
        # ZipInfo keeps the NUL-bearing name in orig_filename but truncates
        # filename. Patch both local and central headers so the visible name
        # would otherwise pass as offline/index.html.
        marker = b"QZQX"
        members = [member("offline/", kind="dir")]
        for name, data in OFFLINE_FILES.items():
            stored = name + marker.decode() if name == "offline/index.html" else name
            members.append(member(stored, data))
        members.append(member(CHECKSUMS, sums_text(OFFLINE_FILES).encode()))
        data = make_zip(members)
        self.assertEqual(data.count(marker), 2)
        patched = data.replace(marker, b"\x00ign")
        self.assertEqual(patched.count(b"\x00ign"), 2)
        self.assert_refused(patched)

    def test_staging_initialization_failure_leaves_no_staging(self):
        data = make_zip(valid_members())
        before = snapshot(self.dest)
        real_mkdir = Path.mkdir

        def fail_site_mkdir(path, *args, **kwargs):
            if path.name == "site":
                raise OSError("injected inode exhaustion")
            return real_mkdir(path, *args, **kwargs)

        with mock.patch.object(Path, "mkdir", fail_site_mkdir):
            with self.assertRaises(OSError):
                self.import_bytes(data)

        self.assertEqual(snapshot(self.dest), before)
        self.assertFalse((self.dest / "site").exists())
        self.assertFalse((self.dest / ".ready").exists())
        self.assertEqual(list(self.dest.glob(".import-staging-*")), [])

    def test_symlink_members_are_refused(self):
        for bad_name in ("offline/link.html", "metadata/link"):
            with self.subTest(name=bad_name):
                members = valid_members() + [
                    member(bad_name, b"index.html", kind="symlink")
                ]
                self.assert_refused(make_zip(members))

    def test_nonregular_members_are_refused(self):
        for bad_name, kind in (
            ("offline/pipe", "fifo"),
            ("raw/device", "char"),
        ):
            with self.subTest(name=bad_name, kind=kind):
                members = valid_members() + [member(bad_name, kind=kind)]
                self.assert_refused(make_zip(members))

    # -- refusal must not disturb existing content ----------------------
    def test_existing_destination_content_is_refused_unchanged(self):
        data = make_zip(valid_members())
        cases = (
            ("site", "dir"),
            (".ready", "file"),
            ("notes.txt", "file"),
            (".import-staging-abandoned", "dir"),
        )
        for name, kind in cases:
            with self.subTest(entry=name):
                dest = self.fresh_destination()
                target = dest / name
                if kind == "dir":
                    target.mkdir()
                    (target / "keep.txt").write_bytes(b"pre-existing")
                else:
                    target.write_bytes(b"pre-existing")
                before = snapshot(dest)
                with self.assertRaises(ValueError):
                    self.module.import_archive(io.BytesIO(data), sha(data), dest)
                # Snapshot equality proves no pre-existing entry was changed or removed.
                self.assertEqual(snapshot(dest), before)

    # -- injectable limits ----------------------------------------------
    def test_compressed_input_limit(self):
        data = make_zip(valid_members())
        self.assert_refused(data, max_compressed_bytes=len(data) - 1)

    def test_expanded_offline_limit(self):
        data = make_zip(valid_members())
        total = sum(len(v) for v in OFFLINE_FILES.values())
        self.assert_refused(data, max_expanded_bytes=total - 1)

    def test_file_count_limit(self):
        data = make_zip(valid_members())
        self.assert_refused(data, max_files=len(OFFLINE_FILES) - 1)

    def test_checksum_metadata_limit(self):
        data = make_zip(valid_members())
        self.assert_refused(data, max_metadata_bytes=len(sums_text(OFFLINE_FILES)) - 1)

    # -- CLI FIFO protocol ----------------------------------------------
    def test_cli_reads_checksum_line_then_zip_from_fifo(self):
        data = make_zip(valid_members())
        dest = self.fresh_destination()
        fifo = self.root / "archive.fifo"
        os.mkfifo(fifo)
        errors = []

        def writer():
            try:
                with open(fifo, "wb") as handle:
                    handle.write(sha(data).encode() + b"\n")
                    handle.write(data)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        with contextlib.redirect_stdout(io.StringIO()):
            code = self.module.main(["--destination", str(dest), "--fifo", str(fifo)])
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive(), "CLI did not drain the FIFO")
        self.assertEqual(errors, [])
        self.assertEqual(code, 0)
        self.assertTrue((dest / "site" / "index.html").is_file())
        self.assertTrue((dest / ".ready").is_file())

    def test_cli_rejects_bad_checksum_line(self):
        data = make_zip(valid_members())
        dest = self.fresh_destination()
        fifo = self.root / "bad.fifo"
        os.mkfifo(fifo)

        def writer():
            with open(fifo, "wb") as handle:
                handle.write(b"not-a-sha256\n")
                handle.write(data)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = self.module.main(["--destination", str(dest), "--fifo", str(fifo)])
        thread.join(timeout=5)

        self.assertEqual(code, 1)
        self.assertIn("refused", stderr.getvalue())
        self.assertFalse((dest / "site").exists())
        self.assertFalse((dest / ".ready").exists())


if __name__ == "__main__":
    unittest.main()
