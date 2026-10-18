"""Bounded, non-decrypting Git index checks; never inspect untracked files.

Only paths and fixed diagnostic text may be logged, never values or parser errors.
Uses the repository-pinned yq for YAML parsing and the Python standard library.
"""

import argparse
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys


IDENTITY = re.compile(
    rb"AGE-(?:SECRET-KEY(?:-[A-Z0-9]+)*|PLUGIN-[A-Z0-9-]+)-1[0-9A-Z]{16,}"
    rb"|-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----",
    re.IGNORECASE,
)
ENCRYPTED = re.compile(
    r"ENC\[AES256_GCM,data:[A-Za-z0-9+/=]*,iv:[A-Za-z0-9+/=]+,"
    r"tag:[A-Za-z0-9+/=]+,type:(?:str|int|float|bool|bytes)\]"
)
MAX_BLOB = 10 * 1024 * 1024


def run(args, **kwargs):
    # Capture stderr: parsers may include the plaintext input in their errors.
    return subprocess.run(args, check=True, capture_output=True, timeout=30, **kwargs).stdout


def encrypted(value):
    return isinstance(value, str) and ENCRYPTED.fullmatch(value) is not None


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate mapping key")
        result[key] = value
    return result


def wrapped_keys(meta):
    # No recipient-policy changes here: accept SOPS' supported backend metadata shapes.
    for backend in ("age", "pgp", "kms", "gcp_kms", "azure_kv", "hc_vault"):
        entries = meta.get(backend)
        if isinstance(entries, list) and entries and all(
            isinstance(entry, dict) and isinstance(entry.get("enc"), str) and entry["enc"].strip()
            for entry in entries
        ):
            return True
    groups = meta.get("key_groups")
    return isinstance(groups, list) and bool(groups) and all(
        isinstance(group, dict) and wrapped_keys(group) for group in groups
    )


def plaintext_free(value, scoped):
    # scoped: every leaf must be encrypted; set for the whole Talos document or below data/stringData.
    if isinstance(value, dict):
        return all(plaintext_free(child, scoped or key in ("data", "stringData")) for key, child in value.items())
    if isinstance(value, list):
        return all(plaintext_free(child, scoped) for child in value)
    return encrypted(value) if scoped else True


def check_sops(content, talos):
    try:
        # Resolve YAML aliases/merge keys before structural checks; one JSON line/document.
        parsed = run(["yq", "eval", "-o=json", "-I=0", "explode(.)", "-"], input=content)
        docs = [json.loads(line, object_pairs_hook=unique_mapping) for line in parsed.splitlines() if line.strip()]
    except (subprocess.SubprocessError, ValueError, OSError):
        return "invalid YAML or YAML parser unavailable (details suppressed)"
    if not docs:
        return "empty SOPS document"
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("sops"), dict):
            return "missing SOPS encryption metadata"
        meta = doc["sops"]
        if not (encrypted(meta.get("mac")) and isinstance(meta.get("version"), str)
                and wrapped_keys(meta)):
            return "incomplete SOPS encryption metadata"
        payload = {key: value for key, value in doc.items() if key != "sops"}
        if talos:
            if not plaintext_free(payload, True):
                return "plaintext value in whole-document Talos SOPS file"
        else:
            # Resource metadata is public; e.g. annotations named 'data' aren't secret fields.
            payload.pop("metadata", None)
            if not plaintext_free(payload, False):
                return "plaintext value under data/stringData"
    return None


def check_index_entry(git, path, file_mode, oid, stage):
    name = PurePosixPath(path).name
    sops = name != ".sops.yaml" and name.endswith((".sops.yaml", ".sops.yml"))
    if name.lower().startswith("age.key"):
        return "forbidden age identity filename"
    if stage != b"0":
        return "unmerged index entry"
    if sops and file_mode not in (b"100644", b"100755"):
        return "SOPS file must be a regular Git blob"
    if file_mode == b"160000":
        # Submodules are commits, not blobs; never walk into them.
        return None

    size = int(run(git + ["cat-file", "-s", oid.decode()]))
    if size > MAX_BLOB:
        return "blob exceeds 10 MiB scan limit; review required (not skipped)"
    content = run(git + ["cat-file", "blob", oid.decode()])
    if IDENTITY.search(content):
        return "private identity/key material detected"
    if sops:
        return check_sops(content, path.startswith("talos/"))
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="check changed index blobs (pre-commit)")
    mode.add_argument("--all", action="store_true", help="check all tracked index blobs (CI)")
    args = parser.parse_args()
    # Running from any directory in the repository must scan the same root-relative paths.
    root = run(["git", "rev-parse", "--show-toplevel"]).decode().strip()
    git = ["git", "-C", root]
    changed = None
    if args.staged:
        changed = set(run(git + ["diff", "--cached", "--name-only", "--diff-filter=ACMRTU", "-z"]).split(b"\0"))
    entries = run(git + ["ls-files", "--stage", "-z"])
    failures = 0
    checked = 0
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        info, name = entry.split(b"\t", 1)
        file_mode, oid, stage = info.split()
        path = name.decode("utf-8", errors="surrogateescape")
        if changed is not None and name not in changed:
            continue
        checked += 1
        reason = check_index_entry(git, path, file_mode, oid, stage)
        if reason:
            # repr escapes embedded newlines/control characters in filenames.
            print(f"secret-check: {ascii(path)}: {reason}", file=sys.stderr)
            failures += 1
    print(f"secret-check: checked {checked} index files; {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (subprocess.SubprocessError, OSError, ValueError, RecursionError):
        print("secret-check: unable to complete scan (details suppressed); refusing success", file=sys.stderr)
        sys.exit(1)
