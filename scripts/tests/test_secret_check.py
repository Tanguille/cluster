"""Synthetic fixtures only: never load the workspace's private files or keys."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import tomllib
import unittest


CHECKER = Path(__file__).resolve().parents[1] / "secret-check.sh"
ENC = "ENC[AES256_GCM,data:ZmFrZQ==,iv:ZmFrZQ==,tag:ZmFrZQ==,type:str]"
META = {"mac": ENC, "version": "3.13.3", "age": [{"recipient": "age1public", "enc": "wrapped"}]}
# Assemble fake tokens so the test source itself contains no identity material.
IDENTITY = "AGE-SECRET-" + "KEY-1" + "A" * 58
PQ_IDENTITY = "AGE-SECRET-" + "KEY-PQ-1" + "A" * 80
PLUGIN_IDENTITY = "AGE-PLUGIN-" + "YUBIKEY-1" + "A" * 40


class SecretCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="secret-check-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "--quiet")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, input=b"", check=True, capture_output=True
        ).stdout

    def stage(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        self.git("add", "--", name)

    def secret(self, value: object = ENC, **extra):
        return json.dumps({"kind": "Secret", "stringData": {"password": value}, "sops": META, **extra})

    def check(self, accepted, mode="--staged"):
        result = subprocess.run(
            ["bash", str(CHECKER), mode], cwd=self.root, capture_output=True, text=True
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0 if accepted else 1, output)
        for sensitive in (IDENTITY, PQ_IDENTITY, PLUGIN_IDENTITY, "do-not-log-this-value"):
            self.assertNotIn(sensitive, output)
        return output

    def test_accept_encrypted_secret_and_public_metadata(self):
        self.stage("secret.sops.yaml", self.secret(metadata={"name": "data", "annotations": {"stringData": "public"}}))
        # Names/comments and ordinary prose are not identity material.
        self.stage("guide.md", "AGE-SECRET-KEY, AGE-SECRET-KEY-PQ variants; age.key backups\n")
        self.check(True)

    def test_root_sops_policy_is_not_an_encrypted_payload(self):
        self.stage(".sops.yaml", "creation_rules: []\n")
        self.check(True)

    def test_reject_duplicate_keys_that_could_hide_plaintext(self):
        self.stage("secret.sops.yaml", f"stringData:\n  password: plaintext\n  password: {ENC}\nsops: {json.dumps(META)}\n")
        self.check(False)

    def test_reject_recipient_metadata_without_wrapped_key(self):
        self.stage("secret.sops.yaml", self.secret(sops={**META, "age": [{"recipient": "age1public"}]}))
        self.check(False)

    def test_reject_fake_encryption_marker(self):
        self.stage("secret.sops.yaml", self.secret("ENC[not really encrypted]"))
        self.check(False)

    def test_reject_private_identities_in_arbitrary_files(self):
        for token in (IDENTITY, PQ_IDENTITY, PLUGIN_IDENTITY, "-----BEGIN OPENSSH " + "PRIVATE KEY-----"):
            with self.subTest(token_type=token[:20]):
                self.stage("notes.txt", token)
                self.check(False)

    def test_reject_age_key_filenames_without_reading_content(self):
        for name in ("age.key", "nested/age.key.backup", "space dir/age.key-pq"):
            with self.subTest(name=name):
                self.stage(name, "synthetic placeholder")
                self.check(False)

    def test_reject_plaintext_and_nonstring_secret_values(self):
        for value in ("do-not-log-this-value", "", 123, True, None, {"nested": "plaintext"}, [ENC, "plaintext"]):
            with self.subTest(value_type=type(value).__name__):
                self.stage("secret.sops.yaml", self.secret(value))
                self.check(False)

    def test_reject_plaintext_data_in_other_documents(self):
        self.stage("secret.sops.yaml", self.secret() + "\n---\n" + self.secret(**{"data": {"token": "plaintext"}}))
        self.check(False)

    def test_reject_missing_or_incomplete_metadata(self):
        for meta in (None, {}, {"version": "3.13.3"}, {**META, "mac": "plaintext"}, {**META, "age": []}):
            with self.subTest(metadata=bool(meta)):
                self.stage("secret.sops.yml", self.secret(sops=meta))
                self.check(False)

    def test_reject_empty_and_invalid_yaml_without_echoing_parser_input(self):
        for content in ("", "stringData: [do-not-log-this-value", "[]", "# only a comment"):
            self.stage("secret.sops.yaml", content)
            self.check(False)

    def test_accept_whole_document_talos_encryption(self):
        self.stage("talos/talsecret.sops.yaml", json.dumps({"cluster": {"id": ENC}, "sops": META}))
        self.check(True)

    def test_reject_plaintext_in_whole_document_talos(self):
        self.stage("talos/talsecret.sops.yaml", json.dumps({"cluster": {"id": "plaintext"}, "sops": META}))
        self.check(False)

    def test_yaml_comments_and_secret_key_names_are_not_plaintext_values(self):
        self.stage("secret.sops.yaml", f"""# data: plaintext is a comment
kind: Secret
metadata:
  name: stringData
data:
  age.key: {ENC}
  stringData: {ENC}
sops: {json.dumps(META)}
""")
        self.check(True)

    def test_yaml_aliases_cannot_hide_plaintext(self):
        self.stage("secret.sops.yaml", f"defaults: &d {{password: plaintext}}\nstringData:\n  <<: *d\nsops: {json.dumps(META)}\n")
        self.check(False)

    def test_partial_staging_reads_index_not_worktree(self):
        self.stage("secret.sops.yaml", self.secret("do-not-log-this-value"))
        (self.root / "secret.sops.yaml").write_text(self.secret())
        self.check(False)
        self.stage("secret.sops.yaml", self.secret())
        (self.root / "secret.sops.yaml").write_text(self.secret("do-not-log-this-value"))
        self.check(True)

    def test_all_scans_unchanged_tracked_blobs_and_ignores_untracked(self):
        self.stage("tracked.txt", IDENTITY)
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "--no-verify", "-m", "fixture")
        (self.root / "age.key.private").write_text(PQ_IDENTITY)
        self.check(True)
        self.check(False, "--all")

    def test_symlink_cannot_bypass_sops_validation(self):
        os.symlink("/nonexistent-private-file", self.root / "secret.sops.yaml")
        self.git("add", "secret.sops.yaml")
        self.check(False)

    def test_deleted_files_are_not_read(self):
        self.stage("safe.txt", "safe")
        self.git("rm", "--force", "safe.txt")
        self.check(True)

    def test_unmerged_index_is_rejected(self):
        oid = self.git("hash-object", "-w", "--stdin").strip().decode()
        entries = "".join(f"100644 {oid} {stage}\tconflict.txt\n" for stage in (1, 2, 3))
        subprocess.run(
            ["git", "update-index", "--index-info"], cwd=self.root,
            input=entries.encode(), check=True, capture_output=True,
        )
        self.check(False)

    def test_safe_index_sops_blobs_do_not_follow_worktree_symlinks(self):
        self.stage("secret.sops.yaml", self.secret())
        (self.root / "secret.sops.yaml").unlink()
        os.symlink("/nonexistent-private-file", self.root / "secret.sops.yaml")
        self.check(True)

    def test_hook_rejects_identity_restaged_by_each_mutator(self):
        config = tomllib.loads((CHECKER.parents[1] / ".lefthook.toml").read_text())
        hook = config["pre-commit"]
        commands = hook["commands"]
        # Reuse actual priorities and serial/parallel policy, replacing only commands/globs.
        mutators = {name for name, command in commands.items() if command.get("stage_fixed")}
        mutators.add("mise-lock")  # This command calls git add itself.
        self.assertEqual(mutators, {"format-just", "format-mise", "format-json", "format-yaml", "mise-lock"})
        self.assertFalse(hook["parallel"])
        (self.root / "restage.py").write_text(
            "from pathlib import Path\nimport subprocess\n"
            f"Path('candidate.txt').write_text({PQ_IDENTITY!r})\n"
            "subprocess.run(['git', 'add', '--', 'candidate.txt'], check=True)\n"
        )
        checker_command = f"bash {shlex.quote(str(CHECKER))} --staged"
        for name in sorted(mutators):
            with self.subTest(mutator=name):
                self.stage("candidate.txt", "safe staged content")
                fixture = "[pre-commit]\nparallel = false\n"
                for job, command in ((name, "python3 restage.py"), ("secret-check", checker_command)):
                    fixture += f"\n[pre-commit.commands.{job}]\n"
                    fixture += f"priority = {commands[job].get('priority', 0)}\n"
                    fixture += f"run = {json.dumps(command)}\n"
                (self.root / ".lefthook.toml").write_text(fixture)
                result = subprocess.run(
                    ["lefthook", "run", "pre-commit", "--no-auto-install", "--no-tty"],
                    cwd=self.root, input=b"", capture_output=True, timeout=30,
                )
                output = (result.stdout + result.stderr).decode()
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn("private identity/key material detected", output)
                self.assertNotIn(PQ_IDENTITY, output)
                self.assertEqual(self.git("show", ":candidate.txt").decode(), PQ_IDENTITY)

    def test_validator_discovers_only_repository_scripts_including_new_files(self):
        source = CHECKER.parents[1] / ".agents/skills/pr-review/scripts/validate-pr.sh"
        validator = self.root / ".agents/skills/pr-review/scripts/validate-pr.sh"
        validator.parent.mkdir(parents=True)
        shutil.copyfile(source, validator)
        self.stage("tracked.sh", "#!/bin/bash\nset -euo pipefail\n")
        self.stage(".gitignore", ".slim/\nignored/\n")
        expected = ["new script.sh", "nested/new\nscript.sh"]
        excluded = [".slim/clonedeps/vendor.sh", "ignored/secret.sh", ".worktrees/lane/a.sh", "nested/worktrees/lane/b.sh"]
        for name in expected + excluded:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/bash\nset -euo pipefail\n")
        # Stub other phases and record shellcheck arguments; this test targets discovery only.
        (self.root / "scripts/tests").mkdir(parents=True)
        (self.root / "scripts/tests/test_fixture.py").write_text(
            "import unittest\nclass Fixture(unittest.TestCase):\n    def test_fixture(self):\n        pass\n"
        )
        (self.root / "scripts/secret-check.sh").write_text("#!/bin/bash\nset -euo pipefail\nexit 0\n")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        stubs = {
            "flate": "#!/bin/bash\nset -euo pipefail\nexit 0\n",
            "shellcheck": "#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\nPath('checked.json').write_text(json.dumps(sys.argv[1:]))\n",
        }
        for name, body in stubs.items():
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        result = subprocess.run(
            ["bash", str(validator)], cwd=self.root, input=b"", capture_output=True,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, timeout=30,
        )
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode())
        checked = set(json.loads((self.root / "checked.json").read_text()))
        wanted = {"tracked.sh", "scripts/secret-check.sh", str(validator.relative_to(self.root)), *expected}
        self.assertEqual(checked, {str(self.root / path) for path in wanted})


if __name__ == "__main__":
    unittest.main(verbosity=2)
