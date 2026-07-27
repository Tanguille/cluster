"""Tests for the HiCache L3 (file backend) rollout PR.

Covers three files changed together in that PR:

  - kubernetes/apps/ai/llmkube/models/qwen36-27b-sglang.yaml
  - docs/llm-hosting/sglang-blockers.md
  - docs/llm-hosting/vllm-vs-sglang-2026-07.md

The Kubernetes manifest is a plain multi-document YAML file. No YAML
library is used anywhere else in this repo's Python code (see
kubernetes/apps/web3/xmrig-guard/app/resources/test_controller.py), so
these tests parse the manifest with plain text/regex, matching the
project's stdlib-only convention, and assert on the exact structural
pieces this PR touched: the new `qwen36-27b-hicache` PVC, the pinned
image digest, the HiCache file-backend env vars, the new volume/mount,
and the `--hicache-write-policy` / `--hicache-storage-backend` args.

The two markdown files are treated as data: this PR made specific
factual claims (arithmetic that must add up, a timestamp delta that
must match its own stated duration, and cross-references to the exact
values baked into the YAML manifest above). Those claims are asserted
here so a future edit that quietly breaks one of them - a `70.7s` -> a
typo'd number, a version bump, a copy-pasted digest that stops matching
the manifest - fails a test instead of just sitting in prose.
"""

import os
import re
import unittest
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
YAML_PATH = os.path.join(
    REPO_ROOT, "kubernetes", "apps", "ai", "llmkube", "models", "qwen36-27b-sglang.yaml"
)
BLOCKERS_PATH = os.path.join(os.path.dirname(__file__), "sglang-blockers.md")
VLLM_VS_SGLANG_PATH = os.path.join(os.path.dirname(__file__), "vllm-vs-sglang-2026-07.md")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _normalize(text):
    """Collapse whitespace/newlines so prose wrapped across lines can be
    matched as a single contiguous phrase."""
    return re.sub(r"\s+", " ", text)


def _split_yaml_documents(text):
    return re.split(r"(?m)^---\s*$", text)


def _extract_spec_block(text, key):
    """Return the body of a top-level (2-space indented) `key:` mapping
    inside a Kubernetes `spec:` section, i.e. everything indented at
    least 4 spaces until the next 2-space-indented key or end of text."""
    pattern = rf"(?m)^  {re.escape(key)}:\n((?:(?:[ \t]{{4,}}.*)?\n)*)"
    match = re.search(pattern, text)
    assert match, f"could not find `{key}:` block in document"
    return match.group(1)


def _extract_list_items(block, item_prefix="    - "):
    """Split a YAML list block on its `- ` markers, returning
    [(first_line_after_dash, rest_of_entry_text), ...]."""
    parts = re.split(r"(?m)^" + re.escape(item_prefix), block)[1:]
    items = []
    for part in parts:
        lines = part.splitlines()
        head = lines[0].strip()
        rest = "\n".join(lines[1:])
        items.append((head, rest))
    return items


def _extract_env(spec_text):
    env_block = _extract_spec_block(spec_text, "env")
    pairs = re.findall(r'- name:\s*(\S+)\s*\n\s+value:\s*"?([^"\n]+?)"?\s*$', env_block, re.M)
    return dict(pairs)


def _extract_flag_args(spec_text):
    args_block = _extract_spec_block(spec_text, "extraArgs")
    return [line.strip().strip('"') for line in re.findall(r"(?m)^    - (.+)$", args_block)]


def _pvc_documents(yaml_text):
    pvcs = {}
    for doc in _split_yaml_documents(yaml_text):
        if "kind: PersistentVolumeClaim" not in doc:
            continue
        name = re.search(r"(?m)^  name:\s*(\S+)", doc).group(1)
        pvcs[name] = doc
    return pvcs


def _inference_service_document(yaml_text):
    for doc in _split_yaml_documents(yaml_text):
        if "kind: InferenceService" in doc:
            return doc
    raise AssertionError("no InferenceService document found")


class YamlTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.yaml_text = _read(YAML_PATH)
        cls.pvcs = _pvc_documents(cls.yaml_text)
        cls.service_doc = _inference_service_document(cls.yaml_text)


class HicachePvcTests(YamlTestCase):
    def test_hicache_pvc_exists_with_expected_spec(self):
        self.assertIn("qwen36-27b-hicache", self.pvcs)
        pvc = self.pvcs["qwen36-27b-hicache"]
        self.assertRegex(pvc, r"(?m)^  namespace:\s*ai\s*$")
        self.assertRegex(pvc, r"(?m)^\s*-\s*ReadWriteOnce\s*$")
        self.assertRegex(pvc, r"(?m)^\s*storageClassName:\s*openebs-hostpath\s*$")
        self.assertRegex(pvc, r"(?m)^\s*storage:\s*64Gi\s*$")

    def test_exactly_three_pvcs_with_expected_unique_names(self):
        self.assertEqual(
            set(self.pvcs),
            {"qwen36-27b-model-cache", "qwen36-27b-triton-cache", "qwen36-27b-hicache"},
        )
        self.assertEqual(len(self.pvcs), 3)

    def test_hicache_pvc_is_distinct_from_triton_cache_pvc(self):
        # Regression: the PR's hicache PVC must be its own claim, not a
        # resize/rename of the pre-existing triton-cache PVC.
        hicache = self.pvcs["qwen36-27b-hicache"]
        triton = self.pvcs["qwen36-27b-triton-cache"]
        self.assertNotEqual(hicache, triton)
        self.assertRegex(triton, r"(?m)^\s*storage:\s*10Gi\s*$")


class ImageDigestTests(YamlTestCase):
    EXPECTED_DIGEST = "72d934d335529934bf73cd480b728cd08ae61178c9204dc37a025302b31fd61e"
    OLD_BROKEN_DIGEST = "c0372d10bdc4baebd50a9726896e214681d703e1ce6c9032bbef0cc4f82bca76"

    def test_image_pinned_to_expected_digest(self):
        match = re.search(
            r"image:\s*ghcr\.io/tanguille/sglang-rdna4:v0\.5\.15-gfx1201@sha256:([0-9a-f]+)",
            self.service_doc,
        )
        self.assertIsNotNone(match, "image digest reference not found")
        self.assertEqual(match.group(1), self.EXPECTED_DIGEST)

    def test_digest_is_well_formed_sha256(self):
        match = re.search(r"@sha256:([0-9a-f]+)", self.service_doc)
        self.assertEqual(len(match.group(1)), 64)

    def test_old_broken_digest_is_not_referenced(self):
        # The prior digest lacked the OpenSSL headers HiCache needs and
        # crashed the scheduler on first prefill; it must not resurface.
        self.assertNotIn(self.OLD_BROKEN_DIGEST, self.yaml_text)


class HicacheEnvVarTests(YamlTestCase):
    def test_expected_hicache_env_vars_and_values(self):
        env = _extract_env(self.service_doc)
        self.assertEqual(env["SGLANG_HICACHE_FILE_BACKEND_STORAGE_DIR"], "/hicache")
        self.assertEqual(env["SGLANG_HICACHE_FILE_BACKEND_MAX_SIZE"], "64Gi")
        self.assertEqual(env["SGLANG_HICACHE_FILE_BACKEND_MIN_FREE_SPACE"], "100Gi")

    def test_max_size_matches_hicache_pvc_capacity(self):
        # The evictor's MAX_SIZE is meant to bound usage within the PVC
        # it lives on; if these drift apart the evictor either can never
        # trigger or triggers long before the PVC is full.
        env = _extract_env(self.service_doc)
        pvc = self.pvcs["qwen36-27b-hicache"]
        pvc_size = re.search(r"(?m)^\s*storage:\s*(\S+)\s*$", pvc).group(1)
        self.assertEqual(env["SGLANG_HICACHE_FILE_BACKEND_MAX_SIZE"], pvc_size)

    def test_min_free_space_exceeds_kubelet_eviction_threshold(self):
        env = _extract_env(self.service_doc)
        min_free_gi = int(re.match(r"(\d+)Gi", env["SGLANG_HICACHE_FILE_BACKEND_MIN_FREE_SPACE"]).group(1))
        # Comment in the manifest documents kubelet's ~50G nodefs
        # eviction threshold; the floor must stay comfortably above it.
        self.assertGreater(min_free_gi, 50)


class HicacheVolumeAndMountTests(YamlTestCase):
    def test_hicache_extra_volume_references_hicache_pvc(self):
        volumes_block = _extract_spec_block(self.service_doc, "extraVolumes")
        items = dict(_extract_list_items(volumes_block, "    - name: "))
        self.assertIn("hicache", items)
        claim = re.search(r"claimName:\s*(\S+)", items["hicache"])
        self.assertIsNotNone(claim)
        self.assertEqual(claim.group(1), "qwen36-27b-hicache")

    def test_hicache_mount_path_is_dedicated(self):
        mounts_block = _extract_spec_block(self.service_doc, "extraVolumeMounts")
        items = dict(_extract_list_items(mounts_block, "    - name: "))
        self.assertIn("hicache", items)
        mount_path = re.search(r"mountPath:\s*(\S+)", items["hicache"])
        self.assertEqual(mount_path.group(1), "/hicache")

    def test_every_extra_volume_has_a_matching_mount_and_vice_versa(self):
        volumes_block = _extract_spec_block(self.service_doc, "extraVolumes")
        mounts_block = _extract_spec_block(self.service_doc, "extraVolumeMounts")
        volume_names = {name for name, _ in _extract_list_items(volumes_block, "    - name: ")}
        mount_names = {name for name, _ in _extract_list_items(mounts_block, "    - name: ")}
        self.assertEqual(volume_names, mount_names)
        self.assertIn("hicache", volume_names)

    def test_mount_paths_are_unique(self):
        mounts_block = _extract_spec_block(self.service_doc, "extraVolumeMounts")
        items = _extract_list_items(mounts_block, "    - name: ")
        paths = [re.search(r"mountPath:\s*(\S+)", rest).group(1) for _, rest in items]
        self.assertEqual(len(paths), len(set(paths)), f"duplicate mount paths: {paths}")
        self.assertIn("/hicache", paths)
        self.assertIn("/cache", paths)


class HicacheExtraArgsTests(YamlTestCase):
    def test_write_policy_is_plain_write_through(self):
        args = _extract_flag_args(self.service_doc)
        self.assertIn("--hicache-write-policy", args)
        idx = args.index("--hicache-write-policy")
        self.assertEqual(args[idx + 1], "write_through")

    def test_write_policy_is_not_selective(self):
        # write_through_selective gates promotion on hit_count >= 2,
        # which is exactly what left L3 empty before this PR.
        args = _extract_flag_args(self.service_doc)
        self.assertNotIn("write_through_selective", args)

    def test_storage_backend_is_file(self):
        args = _extract_flag_args(self.service_doc)
        self.assertIn("--hicache-storage-backend", args)
        idx = args.index("--hicache-storage-backend")
        self.assertEqual(args[idx + 1], "file")

    def test_hicache_flags_appear_as_adjacent_pairs_at_end_of_args(self):
        args = _extract_flag_args(self.service_doc)
        policy_idx = args.index("--hicache-write-policy")
        backend_idx = args.index("--hicache-storage-backend")
        # Both new/changed flags should be well-formed flag/value pairs,
        # not accidentally swallowing a neighboring token.
        self.assertEqual(args[policy_idx + 1], "write_through")
        self.assertEqual(args[backend_idx + 1], "file")
        self.assertNotEqual(policy_idx, backend_idx)


class CrossFileConsistencyTests(unittest.TestCase):
    """Checks that the two markdown docs agree with the manifest they
    describe, so documentation can't silently drift from config."""

    @classmethod
    def setUpClass(cls):
        cls.yaml_text = _read(YAML_PATH)
        cls.service_doc = _inference_service_document(cls.yaml_text)
        cls.blockers_text = _read(BLOCKERS_PATH)
        cls.blockers_norm = _normalize(cls.blockers_text)

    def test_doc_short_digest_is_prefix_of_manifest_digest(self):
        doc_match = re.search(r"image `sha256:([0-9a-f]+)`", self.blockers_norm)
        self.assertIsNotNone(doc_match, "expected short digest reference in sglang-blockers.md")
        yaml_match = re.search(r"@sha256:([0-9a-f]+)", self.service_doc)
        self.assertTrue(yaml_match.group(1).startswith(doc_match.group(1)))

    def test_doc_max_size_and_min_free_space_match_manifest(self):
        doc_match = re.search(
            r"MAX_SIZE`? \(([^)]+)\) and `?MIN_FREE_SPACE`? \(([^)]+)\)", self.blockers_norm
        )
        self.assertIsNotNone(doc_match)
        env = _extract_env(self.service_doc)
        self.assertEqual(doc_match.group(1), env["SGLANG_HICACHE_FILE_BACKEND_MAX_SIZE"])
        self.assertEqual(doc_match.group(2), env["SGLANG_HICACHE_FILE_BACKEND_MIN_FREE_SPACE"])

    def test_doc_pvc_name_matches_manifest(self):
        self.assertIn("qwen36-27b-hicache", self.blockers_norm)
        pvcs = _pvc_documents(self.yaml_text)
        self.assertIn("qwen36-27b-hicache", pvcs)

    def test_doc_write_policy_narrative_matches_manifest_args(self):
        # The doc explains *why* write_through_selective was dropped;
        # the manifest must actually reflect that decision.
        self.assertIn("write_through_selective", self.blockers_norm)
        self.assertIn("Any L3 test must run under plain `write_through`", self.blockers_norm)
        args = _extract_flag_args(self.service_doc)
        self.assertEqual(args[args.index("--hicache-write-policy") + 1], "write_through")


class SglangBlockersDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(BLOCKERS_PATH)
        cls.norm = _normalize(cls.text)

    def test_l3_conclusion_corrected_to_untested_not_failed(self):
        self.assertIn("L3 was untested, not failed.", self.norm)
        self.assertNotIn("L3 is untested, not\n    failed.", self.text)

    def test_retest_section_present_with_all_evidence_rows(self):
        self.assertIn("Retest (2026-07-27): L3 works.", self.norm)
        for row in ("backend initialises", "L3 writes", "L2 saturates", "survives restart"):
            self.assertIn(row, self.norm)

    def test_restart_token_arithmetic_is_internally_consistent(self):
        match = re.search(
            r"([\d,]+) newly backed \+ ([\d,]+) prefetched = ([\d,]+)", self.norm
        )
        self.assertIsNotNone(match, "expected the restart token reconciliation sentence")
        backed, prefetched, total = (int(g.replace(",", "")) for g in match.groups())
        self.assertEqual(backed + prefetched, total)

    def test_old_unqualified_rollback_warning_is_replaced(self):
        # The old sentence unconditionally named the pre-move path;
        # after the PVC move it must describe "that directory" instead
        # and separately call out the orphaned old path.
        old_phrase = "Rollback does not remove `/cache/sglang/hicache`; it can contain"
        self.assertNotIn(old_phrase, self.norm)
        self.assertIn("Rollback does not remove that directory", self.norm)
        self.assertIn("orphaned by this move", self.norm)
        self.assertIn("/cache/sglang/hicache", self.norm)

    def test_direct_io_and_l2_ratio_language_updated(self):
        self.assertIn(
            "Direct I/O and ratio 1.5 L2 remain validated and enabled", self.norm
        )

    def test_hicache_own_pvc_rationale_documented(self):
        self.assertIn(
            "lives on its own `qwen36-27b-hicache` PVC at `/hicache`", self.norm
        )


class VllmVsSglangDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(VLLM_VS_SGLANG_PATH)
        cls.norm = _normalize(cls.text)

    def test_etag_revalidated_message_no_longer_implies_a_304(self):
        # The corrected text must not claim the message follows a 304.
        self.assertNotIn(
            'boot sends `If-None-Match`, gets `304`, and prints `Model artifact', self.norm
        )
        self.assertIn(
            'boot sends `If-None-Match` and prints `Model artifact ... revalidated` over a corrupt',
            self.norm,
        )
        self.assertIn("printed on any curl exit 0, not on a `304`", self.norm)

    def test_truncate_on_open_defect_is_documented(self):
        self.assertIn('opens the destination for writing at request', self.norm)
        self.assertIn("truncated at the start of every restart", self.norm)
        self.assertIn("it is destroyed", self.norm)

    def test_confirmed_restart_duration_matches_its_own_timestamps(self):
        match = re.search(
            r"ran (\d+)m(\d+)s \((\d\d:\d\d:\d\d)Z to (\d\d:\d\d:\d\d)Z\)", self.norm
        )
        self.assertIsNotNone(match, "expected the model-downloader timing sentence")
        minutes, seconds, start, end = match.groups()
        fmt = "%H:%M:%S"
        delta = datetime.strptime(end, fmt) - datetime.strptime(start, fmt)
        claimed = int(minutes) * 60 + int(seconds)
        self.assertEqual(delta.total_seconds(), claimed)

    def test_upstream_fix_recommendation_and_tracking_issue_present(self):
        self.assertIn("HEAD` and compare `Content-Length`", self.norm)
        self.assertIn("download to `$dest.tmp` + `mv`", self.norm)
        self.assertRegex(
            self.text,
            r"\[defilantech/LLMKube#1309\]\(https://github\.com/defilantech/LLMKube/issues/1309\)",
        )

    def test_old_vague_upstream_fix_wording_is_gone(self):
        # The prior text hand-waved at "follow redirects with the
        # conditional intact"; that generic phrasing should not survive
        # alongside the new specific remediation steps.
        self.assertNotIn("follow redirects with the conditional intact", self.norm)


if __name__ == "__main__":
    unittest.main()