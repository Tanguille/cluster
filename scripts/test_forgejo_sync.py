"""Exercise the actual sync script against local Git servers, without credentials/network."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "kubernetes/apps/default/forgejo/app/scripts/sync.sh"


class ForgejoSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.git"
        self.target = self.root / "target.git"
        self.work = self.root / "work"
        self.cache = self.root / "cache.git"
        self.env = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "Sync test",
            "GIT_AUTHOR_EMAIL": "sync@example.invalid",
            "GIT_COMMITTER_NAME": "Sync test",
            "GIT_COMMITTER_EMAIL": "sync@example.invalid",
            "SOURCE_URL": str(self.source),
            "TARGET_URL": str(self.target),
            "CACHE_DIR": str(self.cache),
            "FORGEJO_USER": "test",
            "FORGEJO_TOKEN": "test-token-not-a-secret",
        }
        for repo in (self.source, self.target):
            self.git(self.root, "init", "--bare", "--initial-branch=main", str(repo))
        self.git(self.root, "init", "--initial-branch=main", str(self.work))
        self.git(self.work, "commit", "--allow-empty", "-m", "initial")
        self.git(self.work, "push", str(self.source), "main")
        self.initial = self.ref(self.source)

    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], env=self.env,
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def ref(self, repo, name="main"):
        return self.git(repo, "rev-parse", name)

    def sync(self, succeeds=True):
        result = subprocess.run(["bash", str(SCRIPT)], env=self.env, capture_output=True, text=True)
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_initial_and_incremental_copy_with_tags(self):
        self.sync()
        self.assertEqual(self.ref(self.target), self.initial)
        self.git(self.work, "commit", "--allow-empty", "-m", "update")
        self.git(self.work, "tag", "v1")
        self.git(self.work, "push", str(self.source), "main", "--tags")
        self.sync()
        self.assertEqual(self.ref(self.target), self.ref(self.source))
        self.assertEqual(self.ref(self.target, "v1"), self.ref(self.source, "v1"))
        self.assertNotIn(self.env["FORGEJO_TOKEN"], (self.cache / "config").read_text())

    def test_outage_commit_blocks_stale_source_and_allows_failback(self):
        self.sync()
        self.git(self.work, "commit", "--allow-empty", "-m", "outage work")
        self.git(self.work, "push", str(self.target), "main")
        outage = self.ref(self.target)
        self.sync(succeeds=False)
        self.assertEqual(self.ref(self.target), outage)
        self.git(self.work, "push", str(self.source), "main")
        self.sync()
        self.assertEqual(self.ref(self.source), self.ref(self.target))

    def test_divergence_preserves_main_but_copies_other_branches(self):
        self.sync()
        self.git(self.work, "commit", "--allow-empty", "-m", "forgejo work")
        self.git(self.work, "push", str(self.target), "main")
        outage = self.ref(self.target)
        self.git(self.work, "reset", "--hard", self.initial)
        self.git(self.work, "commit", "--allow-empty", "-m", "github work")
        self.git(self.work, "branch", "new-branch")
        self.git(self.work, "push", str(self.source), "main", "new-branch")
        self.sync(succeeds=False)
        self.assertEqual(self.ref(self.target), outage)
        self.assertEqual(self.ref(self.target, "new-branch"), self.ref(self.source, "new-branch"))

    def test_rebased_renovate_branch_does_not_block_main(self):
        self.git(self.work, "checkout", "-b", "renovate/test")
        self.git(self.work, "commit", "--allow-empty", "-m", "old dependency update")
        self.git(self.work, "push", str(self.source), "renovate/test")
        self.sync()
        original_feature = self.ref(self.target, "renovate/test")
        self.git(self.work, "checkout", "main")
        self.git(self.work, "commit", "--allow-empty", "-m", "new main")
        self.git(self.work, "push", str(self.source), "main")
        self.git(self.work, "checkout", "-B", "renovate/test")
        self.git(self.work, "commit", "--allow-empty", "-m", "rebased dependency update")
        self.git(self.work, "push", "--force", str(self.source), "renovate/test")
        self.sync(succeeds=False)
        self.assertEqual(self.ref(self.target), self.ref(self.source))
        self.assertEqual(self.ref(self.target, "renovate/test"), original_feature)

    def test_deletions_do_not_remove_destination_refs(self):
        self.git(self.work, "branch", "keep")
        self.git(self.work, "tag", "keep-tag")
        self.git(self.work, "push", str(self.source), "keep", "keep-tag")
        self.sync()
        self.git(self.source, "update-ref", "-d", "refs/heads/keep")
        self.git(self.source, "update-ref", "-d", "refs/tags/keep-tag")
        self.sync()
        self.assertEqual(self.ref(self.target, "keep"), self.initial)
        self.assertEqual(self.ref(self.target, "keep-tag"), self.initial)

    def test_rewritten_tag_is_rejected(self):
        self.git(self.work, "tag", "v1")
        self.git(self.work, "push", str(self.source), "v1")
        self.sync()
        self.git(self.work, "commit", "--allow-empty", "-m", "update")
        self.git(self.work, "tag", "-f", "v1")
        self.git(self.work, "push", "--force", str(self.source), "main", "v1")
        self.sync(succeeds=False)
        self.assertEqual(self.ref(self.target, "v1"), self.initial)
        self.assertEqual(self.ref(self.target), self.ref(self.source))

    def test_failed_fetch_never_pushes_cached_data(self):
        self.sync()
        # A target-side branch is absent from a stale source cache.
        self.git(self.target, "update-ref", "refs/heads/outage", self.initial)
        self.env["SOURCE_URL"] = str(self.root / "missing.git")
        self.sync(succeeds=False)
        self.assertEqual(self.ref(self.target, "outage"), self.initial)
        self.assertEqual(self.ref(self.target), self.initial)


if __name__ == "__main__":
    unittest.main()
