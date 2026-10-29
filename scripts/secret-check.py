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


def is_sops(path):
    # .sops.yaml is the (possibly nested) creation-rule config, not an encrypted payload.
    name = PurePosixPath(path).name
    return name != ".sops.yaml" and name.endswith((".sops.yaml", ".sops.yml"))


def check_index_entry(path, file_mode, stage):
    """Checks that need no blob content; returns (reason, needs_blob)."""
    if PurePosixPath(path).name.lower().startswith("age.key"):
        return "forbidden age identity filename", False
    if stage != b"0":
        return "unmerged index entry", False
    if is_sops(path) and file_mode not in (b"100644", b"100755"):
        return "SOPS file must be a regular Git blob", False
    # Submodules are commits, not blobs; never walk into them.
    return None, file_mode != b"160000"


def read_blobs(git, oids):
    """Map oid to content (None above MAX_BLOB) with two cat-file processes for the whole index."""
    oids = list(dict.fromkeys(oids))
    if not oids:
        return {}
    request = b"".join(oid + b"\n" for oid in oids)
    sizes = dict(line.split() for line in run(git + ["cat-file", "--batch-check=%(objectname) %(objectsize)"], input=request).splitlines())
    blobs = {oid: None for oid in oids}
    small = [oid for oid in oids if int(sizes[oid]) <= MAX_BLOB]
    if small:
        out = run(git + ["cat-file", "--batch=%(objectname) %(objectsize)"], input=b"".join(oid + b"\n" for oid in small))
        pos = 0
        for _ in small:
            newline = out.index(b"\n", pos)
            oid, size = out[pos:newline].split()
            start = newline + 1
            blobs[oid] = out[start:start + int(size)]
            pos = start + int(size) + 1
    return blobs


def check_content(path, content):
    if content is None:
        return "blob exceeds 10 MiB scan limit; review required (not skipped)"
    if IDENTITY.search(content):
        return "private identity/key material detected"
    if is_sops(path):
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
    reasons = {}
    pending = []
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        info, name = entry.split(b"\t", 1)
        file_mode, oid, stage = info.split()
        path = name.decode("utf-8", errors="surrogateescape")
        if changed is not None and name not in changed:
            continue
        reasons[path], needs_blob = check_index_entry(path, file_mode, stage)
        if not reasons[path] and needs_blob:
            pending.append((path, oid))
    blobs = read_blobs(git, [oid for _, oid in pending])
    for path, oid in pending:
        reasons[path] = check_content(path, blobs[oid])
    failures = 0
    for path, reason in reasons.items():
        if reason:
            # ascii() escapes newlines, control and non-ASCII characters.
            print(f"secret-check: {ascii(path)}: {reason}", file=sys.stderr)
            failures += 1
    print(f"secret-check: checked {len(reasons)} index files; {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (subprocess.SubprocessError, OSError, ValueError, RecursionError):
        print("secret-check: unable to complete scan (details suppressed); refusing success", file=sys.stderr)
        sys.exit(1)
