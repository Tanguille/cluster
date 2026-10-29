"""A home.arpa flag label grants or opts out only through a Cilium policy selector, so a typo'd key grants nothing."""

import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
# Near-miss domains match too; outside policies only JSON keys ('":' suffix) set a flag.
FLAG = r'"([\w.-]*(?:home\.|\.arpa)[\w.-]*/[\w.-]+)"'
POLICIES = {"CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy"}
POLICY = """---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
spec:
  endpointSelector:
    matchExpressions:
      - key: egress.home.arpa/lan
        operator: NotIn
        values: [allow]
"""
# The live JJGadgets typo: the key looks like a flag but no selector names it.
WORKLOAD = """---
kind: HelmRelease
spec:
  values:
    podLabels:
      egress.home.arpa/lan: allow
      egress.home.apa/kps: allow
"""


def unselected(root):
    files = sorted(map(str, Path(root).rglob("*.yaml")))
    out = subprocess.run(["yq", "-o=json", "-I=0", ".", *files], check=True, capture_output=True, text=True, timeout=30).stdout
    used, selected = set(), set()
    for line in out.splitlines():
        doc = json.loads(line)
        policy = isinstance(doc, dict) and doc.get("kind") in POLICIES
        (selected if policy else used).update(re.findall(FLAG if policy else FLAG + ":", line))
    return sorted(used - selected)


class LabelFlagTests(unittest.TestCase):
    def test_repository_flags_are_all_selected(self):
        self.assertEqual(unselected(ROOT / "kubernetes"), [])

    def test_typo_flag_is_listed(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "policy.yaml").write_text(POLICY)
            (Path(root) / "workload.yaml").write_text(WORKLOAD)
            self.assertEqual(unselected(root), ["egress.home.apa/kps"])


if __name__ == "__main__":
    unittest.main()
