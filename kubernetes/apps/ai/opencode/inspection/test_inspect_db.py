"""Offline synthetic fixtures only. Never point these tests at runtime state."""

import contextlib
import hashlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import inspect_db


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="opencode-inspect-")
        self.path = Path(self.tmp.name) / "opencode.db"
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""
            CREATE TABLE project (id TEXT PRIMARY KEY);
            CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT REFERENCES project(id),
                                  payload TEXT DEFAULT 'SECRET_DEFAULT');
            CREATE INDEX session_project ON session(project_id);
            CREATE VIEW hidden_view AS SELECT 'SECRET_VIEW';
            CREATE TRIGGER hidden_trigger AFTER INSERT ON project BEGIN
                SELECT 'SECRET_TRIGGER'; END;
            INSERT INTO project VALUES ('SECRET_PROJECT_ID');
            INSERT INTO session VALUES ('SECRET_SESSION_ID', 'SECRET_PROJECT_ID', 'SECRET_JSON_PAYLOAD');
        """)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def capture(self, path=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = inspect_db.main(path or self.path)
        return code, output.getvalue()

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.path.parent.iterdir() if p.is_file()}

    def test_metadata_only_and_source_unchanged(self):
        before = self.hashes()
        code, output = self.capture()
        self.assertEqual(code, 0)
        self.assertNotIn("SECRET", output)
        self.assertNotIn("CREATE", output)
        self.assertNotIn("DEFAULT", output)
        self.assertNotIn("Traceback", output)
        self.assertIn("count session 1", output)
        self.assertIn("fk session project project_id id", output)
        self.assertIn("integrity ok", output)
        self.assertEqual(before, self.hashes())

    def test_wal_rows_included_without_db_or_wal_changes(self):
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("INSERT INTO project VALUES ('SECRET_WAL_ROW')")
        self.db.commit()
        before = self.hashes()
        code, output = self.capture()
        self.assertEqual(code, 0)
        self.assertIn("count project 2", output)
        self.assertNotIn("SECRET", output)
        after = self.hashes()
        # SHM is SQLite's lock/index coordination, not database payload. The live
        # read-only volume is the additional enforcement against SHM writes.
        for name in ("opencode.db", "opencode.db-wal"):
            self.assertEqual(before[name], after[name])
        self.assertEqual(set(before), set(after))

    def test_missing_database_is_not_created(self):
        path = self.path.parent / "missing.db"
        self.assertEqual(self.capture(path), (1, "inspection failed-closed\n"))
        self.assertFalse(path.exists())

    def test_failure_does_not_log_exception_values(self):
        with mock.patch.object(inspect_db, "inspect", side_effect=RuntimeError("SECRET_SQL_VALUE")):
            self.assertEqual(self.capture(), (1, "inspection failed-closed\n"))

    def test_fk_violation_only_aggregate(self):
        self.db.execute("INSERT INTO session VALUES ('SECRET_BAD_ID', 'SECRET_MISSING', 'SECRET_BAD_PAYLOAD')")
        self.db.commit()
        code, output = self.capture()
        self.assertEqual(code, 1)
        self.assertIn("foreign-key-violations 1", output)
        self.assertNotIn("SECRET", output)

    def test_fingerprint_excludes_default_literals_and_rows(self):
        before = next(x for x in self.capture()[1].splitlines() if x.startswith("structural-sha256"))
        self.db.execute("INSERT INTO project VALUES ('SECRET_EXTRA_ROW')")
        self.db.commit()
        after = next(x for x in self.capture()[1].splitlines() if x.startswith("structural-sha256"))
        self.assertEqual(before, after)
        other = self.path.parent / "other.db"
        with sqlite3.connect(other) as db:
            schema = ";\n".join(row[0] for row in self.db.execute(
                "SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY rowid"))
            db.executescript(schema.replace("SECRET_DEFAULT", "OTHER_SECRET_DEFAULT"))
        alternate = next(x for x in self.capture(other)[1].splitlines() if x.startswith("structural-sha256"))
        self.assertEqual(before, alternate)

    def test_unsupported_identifier_fails_without_partial_output(self):
        self.db.execute('CREATE TABLE "SECRET\nIDENTIFIER" (id TEXT)')
        self.db.commit()
        self.assertEqual(self.capture(), (1, "inspection failed-closed\n"))

    def test_connection_is_readonly_and_one_transaction(self):
        original = sqlite3.connect
        statements = []

        def connect(filename, **kwargs):
            self.assertTrue(filename.endswith("?mode=ro"))
            self.assertNotIn("immutable", filename)
            db = original(filename, **kwargs)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("CREATE TABLE forbidden (id TEXT)")
            db.set_trace_callback(statements.append)  # Synthetic test only; never live logs.
            return db

        with mock.patch.object(inspect_db.sqlite3, "connect", side_effect=connect):
            self.assertEqual(self.capture()[0], 0)
        self.assertEqual(statements.count("BEGIN"), 1)
        self.assertEqual(statements.count("ROLLBACK"), 1)
        self.assertIn("PRAGMA query_only=ON", statements)


if __name__ == "__main__":
    unittest.main()
