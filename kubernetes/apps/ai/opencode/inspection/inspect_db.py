"""Metadata only: no OpenCode bootstrap, exports, defaults, DDL or row payloads."""

import hashlib
from pathlib import Path
import re
import sqlite3
import sys
import time


def token(value):
    """Reject unusual identifiers rather than echoing untrusted schema text."""
    if value is None:
        return "-"
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9 ]{0,127}", value):
        raise ValueError("unsupported metadata")
    return value


def inspect(path):
    # mode=ro refuses missing files. Never use immutable on a changing WAL DB.
    db = sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.enable_load_extension(False)
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        deadline = time.monotonic() + 240
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute("BEGIN")
        structural = []
        counts = []
        tables = db.execute("SELECT name, type FROM sqlite_schema ORDER BY type, name").fetchall()
        # Virtual tables could invoke application code; no such fallback is permitted.
        if any(row[2] in ("virtual", "shadow") for row in db.execute("PRAGMA table_list")):
            raise ValueError("unsupported virtual schema")
        for name, kind in tables:
            name, kind = token(name), token(kind)
            structural.append(f"object {kind} {name}")
            if kind != "table":
                continue
            # Select metadata fields explicitly: never retrieve dflt_value or raw DDL.
            for col in db.execute(
                'SELECT name, type, "notnull", pk, hidden FROM pragma_table_xinfo(?) ORDER BY cid',
                (name,),
            ):
                structural.append(f"column {name} {token(col[0])} {token(col[1])} notnull={col[2]} pk={col[3]} hidden={col[4]}")
            for fk in db.execute(
                'SELECT "table", "from", "to", on_update, on_delete, match FROM pragma_foreign_key_list(?) ORDER BY id, seq',
                (name,),
            ):
                structural.append("fk " + name + " " + " ".join(token(v) for v in fk))
            for idx in db.execute(
                'SELECT name, "unique", origin, partial FROM pragma_index_list(?) ORDER BY name', (name,)
            ):
                index = token(idx[0])
                structural.append(f"index {name} {index} unique={idx[1]} origin={token(idx[2])} partial={idx[3]}")
                for col in db.execute(
                    'SELECT name, "desc", coll, key FROM pragma_index_xinfo(?) ORDER BY seqno', (index,)
                ):
                    structural.append(f"index-column {index} {token(col[0])} desc={col[1]} coll={token(col[2])} key={col[3]}")
            count = db.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0]
            counts.append(f"count {name} {count}")
        integrity_ok = all(row[0] == "ok" for row in db.execute("PRAGMA integrity_check"))
        fk_count = sum(1 for _ in db.execute("PRAGMA foreign_key_check"))
        fingerprint = hashlib.sha256("\n".join(structural).encode()).hexdigest()
        db.rollback()  # End read transaction only; no database writes.
        lines = ["inspection-format 1", "sqlite-version " + sqlite3.sqlite_version]
        lines += structural + counts
        lines += ["structural-sha256 " + fingerprint,
                  "integrity " + ("ok" if integrity_ok else "failed"),
                  f"foreign-key-violations {fk_count}"]
        return lines, integrity_ok and fk_count == 0
    finally:
        db.close()


def main(path: str | Path = "/source/opencode.db"):
    try:
        lines, healthy = inspect(path)
    except Exception:
        # Exception/SQL text can contain values; never emit it or a traceback.
        print("inspection failed-closed", flush=True)
        return 1
    print("\n".join(lines), flush=True)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
