"""CSI readAffinity needs libceph d6c7a5fcd088 on every node, or Ceph v21 silently drops CephFS writes."""

import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
# First stable kernel whose libceph sets read_from_replica on read requests only.
MIN_KERNEL = (7, 3, 4)


class ReadAffinityKernelTests(unittest.TestCase):
    def test_kernel_floor_while_read_affinity_is_on(self):
        cluster = (ROOT / "kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml").read_text()
        enabled = re.search(r"readAffinity:\s*\n\s*enabled:\s*(true|false)", cluster).group(1)
        if enabled == "false":
            self.skipTest("readAffinity is off")
        upgrade = (ROOT / "kubernetes/apps/system-upgrade/tuppr/upgrades/talosupgrade.yaml").read_text()
        version = re.search(r"^\s*version:\s*(\S+)", upgrade, re.M).group(1)
        kernel = re.search(r"-k(\d+)\.(\d+)\.(\d+)$", version)
        # A stock Talos target names no kernel: look up its linux_version in siderolabs/pkgs and teach this test.
        self.assertIsNotNone(kernel, f"cannot tell the kernel of Talos target {version}")
        self.assertGreaterEqual(tuple(map(int, kernel.groups())), MIN_KERNEL)


if __name__ == "__main__":
    unittest.main()
