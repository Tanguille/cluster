#!/usr/bin/env python3
"""Bounded importer for the private family-blog archive.

The ZIP is an immutable recovery source. This module only reads it, verifies
its checksum and per-file checksums, and publishes the verified ``offline/``
tree as ``<destination>/site``. Family content never enters this repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
import zipfile
from pathlib import Path, PurePosixPath

MAX_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_FILES = 10_000
MAX_METADATA_BYTES = 8 * 1024 * 1024

FIFO_PATH = Path("/tmp/archive.fifo")
DESTINATION = Path("/data")

_OFFLINE_PREFIX = "offline/"
_CHECKSUM_NAME = "metadata/SHA256SUMS.txt"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SUM_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})  (.+)$")
_CHUNK = 1024 * 1024


class ImportRejected(ValueError):
    """The archive or destination cannot be imported safely."""


def _normalise_sha(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value.strip()):
        raise ImportRejected(f"{label} must be 64 hexadecimal characters")
    return value.strip().lower()


def _check_destination(destination: Path) -> None:
    if not destination.exists():
        raise ImportRejected(f"destination {destination} does not exist")
    if not destination.is_dir():
        raise ImportRejected(f"destination {destination} is not a directory")
    for entry in destination.iterdir():
        if entry.name == "lost+found":
            if not entry.is_dir():
                raise ImportRejected(f"{entry} is not a directory")
            continue
        raise ImportRejected(
            f"destination {destination} is not empty (found {entry.name!r}); "
            "refusing to overwrite or clean existing content"
        )


def _validate_member_name(name: str) -> None:
    if not name or "\x00" in name:
        raise ImportRejected("archive member name is empty or contains NUL")
    if "\\" in name:
        raise ImportRejected(f"archive member name contains a backslash: {name!r}")
    if name.startswith("/") or PurePosixPath(name).is_absolute():
        raise ImportRejected(f"archive member name is absolute: {name!r}")
    if re.match(r"^[A-Za-z]:", name):
        raise ImportRejected(f"archive member name has a drive prefix: {name!r}")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in name):
        raise ImportRejected(f"archive member name contains control characters: {name!r}")
    parts = name.split("/")
    if name.endswith("/"):
        parts = parts[:-1]
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ImportRejected(f"archive member name is not a safe relative path: {name!r}")


def _member_kind(info: zipfile.ZipInfo) -> str:
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        raise ImportRejected(f"archive member is a symlink: {info.filename!r}")
    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ImportRejected(
            f"archive member is not a regular file or directory: {info.filename!r}"
        )
    if info.is_dir() or file_type == stat.S_IFDIR:
        if file_type == stat.S_IFREG:
            raise ImportRejected(
                f"archive member is a file but has a directory name: {info.filename!r}"
            )
        return "dir"
    return "file"


def _inspect_archive(
    archive: zipfile.ZipFile,
    max_files: int,
    max_expanded_bytes: int,
) -> tuple[zipfile.ZipInfo, dict[str, zipfile.ZipInfo]]:
    seen: set[str] = set()
    offline: dict[str, zipfile.ZipInfo] = {}
    checksum_info = None
    for info in archive.infolist():
        name = info.filename
        # ZipInfo truncates filename at the first NUL while orig_filename keeps
        # the header bytes; validate the original and reject normalization.
        if info.orig_filename != name:
            raise ImportRejected(
                f"archive member name was normalized or truncated: {info.orig_filename!r}"
            )
        _validate_member_name(info.orig_filename)
        if name in seen:
            raise ImportRejected(f"duplicate archive member: {name!r}")
        seen.add(name)
        if info.flag_bits & 0x1:
            raise ImportRejected(f"encrypted archive member is not supported: {name!r}")
        kind = _member_kind(info)
        if kind == "dir":
            continue
        if name == _CHECKSUM_NAME:
            checksum_info = info
        elif name.startswith(_OFFLINE_PREFIX):
            offline[name] = info

    if checksum_info is None:
        raise ImportRejected(f"archive is missing {_CHECKSUM_NAME}")
    if len(offline) > max_files:
        raise ImportRejected(
            f"archive has {len(offline)} offline files; limit is {max_files}"
        )
    expanded = sum(info.file_size for info in offline.values())
    if expanded > max_expanded_bytes:
        raise ImportRejected(
            f"offline content expands to {expanded} bytes; limit is {max_expanded_bytes}"
        )
    return checksum_info, offline


def _read_checksum_metadata(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    max_metadata_bytes: int,
) -> str:
    if info.file_size > max_metadata_bytes:
        raise ImportRejected(
            f"{_CHECKSUM_NAME} is {info.file_size} bytes; limit is {max_metadata_bytes}"
        )
    try:
        with archive.open(info) as handle:
            data = handle.read(max_metadata_bytes + 1)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ImportRejected(f"cannot read {_CHECKSUM_NAME}: {exc}") from exc
    if len(data) > max_metadata_bytes:
        raise ImportRejected(
            f"{_CHECKSUM_NAME} exceeds the {max_metadata_bytes}-byte limit"
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportRejected(f"{_CHECKSUM_NAME} is not valid UTF-8") from exc


def _parse_checksums(text: str) -> dict[str, str]:
    """Parse the archive-root-relative SHA256SUMS manifest.

    Every entry is validated for safe relative syntax and duplicate paths are
    rejected. Only ``offline/`` entries participate in the exact inventory
    comparison, because README/metadata/raw/reports entries are intentionally
    never extracted. Paths are relative to the archive root, matching the
    manifest produced alongside the real archive.
    """
    entries: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = _SUM_LINE_RE.match(line)
        if match is None:
            raise ImportRejected(
                f"{_CHECKSUM_NAME}:{line_number}: expected '<sha256>  <path>'"
            )
        digest = match.group(1).lower()
        listed = match.group(2)
        if listed.endswith("/"):
            raise ImportRejected(
                f"{_CHECKSUM_NAME}:{line_number}: expected a file path, got {listed!r}"
            )
        _validate_member_name(listed)
        if listed in entries:
            raise ImportRejected(
                f"{_CHECKSUM_NAME}:{line_number}: duplicate entry for {listed!r}"
            )
        entries[listed] = digest
    if not entries:
        raise ImportRejected(f"{_CHECKSUM_NAME} lists no files")
    offline = {
        name: digest
        for name, digest in entries.items()
        if name.startswith(_OFFLINE_PREFIX)
    }
    if not offline:
        raise ImportRejected(f"{_CHECKSUM_NAME} lists no offline files")
    return offline


def _extract_offline(
    archive: zipfile.ZipFile,
    offline: dict[str, zipfile.ZipInfo],
    expected: dict[str, str],
    site_root: Path,
    max_expanded_bytes: int,
) -> int:
    extracted = 0
    for name in sorted(offline):
        info = offline[name]
        relative = name[len(_OFFLINE_PREFIX) :]
        target = site_root.joinpath(*relative.split("/"))
        digest = hashlib.sha256()
        written = 0
        try:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            with archive.open(info) as source, open(target, "xb") as sink:
                while True:
                    chunk = source.read(_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    extracted += len(chunk)
                    if written > info.file_size or extracted > max_expanded_bytes:
                        raise ImportRejected(
                            f"{name}: expanded content exceeds the declared or allowed size"
                        )
                    digest.update(chunk)
                    sink.write(chunk)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ImportRejected(f"cannot extract {name!r}: {exc}") from exc
        if written != info.file_size:
            raise ImportRejected(f"{name!r}: extracted size does not match the archive")
        if digest.hexdigest() != expected[name]:
            raise ImportRejected(f"{name!r}: extracted content checksum mismatch")
        os.chmod(target, 0o644)

    for directory in sorted(site_root.rglob("*"), key=lambda path: len(path.parts)):
        if directory.is_dir():
            os.chmod(directory, 0o755)
    os.chmod(site_root, 0o755)
    return extracted


def _spool_archive(stream, target: Path, max_compressed_bytes: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with open(target, "xb") as sink:
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray)):
                raise ImportRejected("archive stream must be binary")
            total += len(chunk)
            if total > max_compressed_bytes:
                raise ImportRejected(
                    f"compressed archive exceeds the {max_compressed_bytes}-byte limit"
                )
            digest.update(chunk)
            sink.write(chunk)
    return digest.hexdigest()


def import_archive(
    stream,
    expected_sha256: str,
    destination: Path,
    *,
    max_compressed_bytes: int = MAX_COMPRESSED_BYTES,
    max_expanded_bytes: int = MAX_EXPANDED_BYTES,
    max_files: int = MAX_FILES,
    max_metadata_bytes: int = MAX_METADATA_BYTES,
) -> dict:
    """Verify a ZIP stream and publish its ``offline/`` tree to ``destination``.

    ``destination`` must be an empty directory (only a filesystem-created
    ``lost+found`` is permitted). On refusal nothing outside a staging
    directory created by this call is modified, and that staging directory is
    removed.
    """
    expected_sha256 = _normalise_sha(expected_sha256, "expected SHA256")
    for label, value in (
        ("max_compressed_bytes", max_compressed_bytes),
        ("max_expanded_bytes", max_expanded_bytes),
        ("max_files", max_files),
        ("max_metadata_bytes", max_metadata_bytes),
    ):
        if not isinstance(value, int) or value <= 0:
            raise ImportRejected(f"{label} must be a positive integer")

    destination = Path(destination)
    _check_destination(destination)

    work = destination / f".import-staging-{uuid.uuid4().hex}"
    work.mkdir(mode=0o700)
    site_staging = work / "site"
    archive_path = work / "archive.zip"
    ready_temp: Path | None = None
    published = False

    try:
        site_staging.mkdir(mode=0o755)
        actual_sha256 = _spool_archive(stream, archive_path, max_compressed_bytes)
        if actual_sha256 != expected_sha256:
            raise ImportRejected(
                f"archive SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
            )

        try:
            archive = zipfile.ZipFile(archive_path)
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            raise ImportRejected("archive is truncated or is not a valid ZIP") from exc

        with archive:
            checksum_info, offline = _inspect_archive(
                archive, max_files, max_expanded_bytes
            )
            checksum_text = _read_checksum_metadata(
                archive, checksum_info, max_metadata_bytes
            )
            expected = _parse_checksums(checksum_text)

            missing = sorted(set(expected) - set(offline))
            unlisted = sorted(set(offline) - set(expected))
            if missing or unlisted:
                raise ImportRejected(
                    "offline files and checksum inventory disagree: "
                    f"missing={missing[:5]} unlisted={unlisted[:5]}"
                )

            index = offline.get(_OFFLINE_PREFIX + "index.html")
            if index is None:
                raise ImportRejected("archive has no offline/index.html")
            if index.file_size == 0:
                raise ImportRejected("archive has an empty offline/index.html")

            extracted_bytes = _extract_offline(
                archive, offline, expected, site_staging, max_expanded_bytes
            )

        archive_path.unlink()
        if (destination / "site").exists() or (destination / ".ready").exists():
            raise ImportRejected(
                "destination gained site/ or .ready during import; refusing to overwrite"
            )
        os.replace(site_staging, destination / "site")
        published = True
        work.rmdir()

        ready_temp = destination / f".ready.tmp-{uuid.uuid4().hex}"
        ready_temp.write_text(
            json.dumps({"files": len(offline), "bytes": extracted_bytes}) + "\n",
            encoding="utf-8",
        )
        os.chmod(ready_temp, 0o644)
        os.replace(ready_temp, destination / ".ready")
        ready_temp = None
        published = False
        return {"files": len(offline), "bytes": extracted_bytes}
    except BaseException:
        if published:
            shutil.rmtree(destination / "site", ignore_errors=True)
        if ready_temp is not None:
            try:
                ready_temp.unlink()
            except OSError:
                pass
        shutil.rmtree(work, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """Read ``<sha256>\\n<zip bytes>`` from a FIFO and import into the volume."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=DESTINATION)
    parser.add_argument("--fifo", type=Path, default=FIFO_PATH)
    args = parser.parse_args(argv)

    fifo: Path = args.fifo
    created = False
    try:
        if fifo.exists():
            if not stat.S_ISFIFO(fifo.stat().st_mode):
                print(f"error: {fifo} exists and is not a FIFO", file=sys.stderr)
                return 1
        else:
            fifo.parent.mkdir(parents=True, exist_ok=True)
            os.mkfifo(fifo, 0o600)
            created = True

        with open(fifo, "rb") as stream:
            line = stream.readline(4096)
            if not line.endswith(b"\n"):
                raise ImportRejected("missing newline-terminated SHA256 line")
            try:
                expected_sha256 = line.rstrip(b"\r\n").decode("ascii")
            except UnicodeDecodeError as exc:
                raise ImportRejected("SHA256 line is not ASCII") from exc
            result = import_archive(stream, expected_sha256, args.destination)

        print(f"imported {result['files']} files, {result['bytes']} bytes")
        return 0
    except ImportRejected as exc:
        print(f"import refused: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if created:
            try:
                fifo.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
