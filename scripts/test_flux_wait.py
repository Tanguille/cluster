"""A dependsOn edge only waits for the target's HelmRelease and CRDs when the target sets wait or healthChecks (kubernetes/AGENTS.md)."""

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


def kustomizations():
    for path in sorted((ROOT / "kubernetes/apps").rglob("ks.yaml")):
        out = subprocess.run(["yq", "-o=json", "-I0", "."], input=path.read_text(), capture_output=True, text=True, check=True).stdout
        for line in filter(None, out.splitlines()):
            doc = json.loads(line)
            if doc.get("kind") == "Kustomization":
                yield path.relative_to(ROOT), doc


class FluxWaitTests(unittest.TestCase):
    def test_depends_on_targets_gate_on_ready(self):
        docs = {}
        for path, doc in kustomizations():
            ns = doc["metadata"].get("namespace") or path.parts[2]
            key = (ns, doc["metadata"]["name"])
            self.assertNotIn(key, docs, f"{path}: duplicate Kustomization {key[0]}/{key[1]}")
            docs[key] = (path, doc["spec"])
        unready = set()
        for (ns, _), (path, spec) in docs.items():
            for dep in spec.get("dependsOn") or []:
                key = (dep.get("namespace", ns), dep["name"])
                self.assertIn(key, docs, f"{path}: dependsOn target missing")
                target_path, target = docs[key]
                if not (target.get("wait") or target.get("healthChecks")):
                    unready.add(f"{target_path}: {key[1]}")
        self.assertEqual(sorted(unready), [], "dependsOn targets without wait: true or healthChecks")


if __name__ == "__main__":
    unittest.main()
