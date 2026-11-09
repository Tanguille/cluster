"""Every PDB-guarded workload needs a priority class, or a drain's evictees preempt a sibling and deadlock the drain."""

import re
from pathlib import Path
import unittest


APPS = Path(__file__).resolve().parents[1] / "kubernetes/apps"
# Chart defaults are already system-* (verified live 2026-11-09); deadvalues forbids restating them.
ALLOWLIST = {"kube-system/metrics-server/app", "system-upgrade/tuppr/app"}
# Operator CRs whose pods a PDB guards; checked per file so one CR's class can't cover another.
PDB_CRS = re.compile(r"^apiVersion:\s*(postgresql\.cnpg\.io|dragonflydb\.io)/\S+\s*\nkind:\s*(Cluster|Pooler|Dragonfly)\s*$", re.M)


class PdbPriorityTests(unittest.TestCase):
    # ponytail: file/dir grep, not a render; a raw PDB passes if any sibling file sets a class.
    # The live DaemonSetPodWithoutPriorityClass alert covers DaemonSets.
    def test_pdb_declarations_have_a_priority_class(self):
        checked = 0
        for path in sorted(APPS.rglob("*.yaml")):
            if str(path.parent.relative_to(APPS)) in ALLOWLIST:
                continue
            text = path.read_text()
            same_file = "podDisruptionBudget:" in text or PDB_CRS.search(text)
            if not (same_file or re.search(r"^kind:\s*PodDisruptionBudget\s*$", text, re.M)):
                continue
            checked += 1
            with self.subTest(path=str(path.relative_to(APPS))):
                scope = [text] if same_file else [p.read_text() for p in path.parent.glob("*.yaml")]
                self.assertTrue(any("priorityClassName:" in s for s in scope))
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
