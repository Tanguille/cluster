#!/usr/bin/env python3
"""Generate the kernel SPDX document consumed by the Talos imager.

Mirrors the `sbom:` block from siderolabs/pkgs `kernel/kernel/pkg.yaml` that
bldr would emit. Fixed timestamp on purpose: a moving date would break build
reproducibility.

Usage: generate-spdx.py <kernel-version>
Writes /rootfs/usr/share/spdx/kernel.spdx.json
"""

import json
import sys


def build_doc(kernel_version: str) -> dict:
    major = kernel_version.split(".")[0]
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "kernel",
        "documentNamespace": f"https://kernel.org/linux-{kernel_version}",
        "creationInfo": {
            "created": "1970-01-01T00:00:00Z",
            "creators": ["Tool: bldr"],
        },
        "packages": [
            {
                "SPDXID": "SPDXRef-Package-kernel",
                "name": "kernel",
                "versionInfo": kernel_version,
                "downloadLocation": (
                    f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/"
                    f"linux-{kernel_version}.tar.xz"
                ),
                "licenseConcluded": "GPL-2.0-only",
                "licenseDeclared": "GPL-2.0-only",
                "copyrightText": "NOASSERTION",
                "filesAnalyzed": False,
                "externalRefs": [
                    {
                        "referenceCategory": "SECURITY",
                        "referenceType": "cpe23Type",
                        "referenceLocator": (
                            f"cpe:2.3:o:linux:linux_kernel:{kernel_version}"
                            ":*:*:*:*:*:*:*"
                        ),
                    }
                ],
            }
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": "SPDXRef-Package-kernel",
                "relationshipType": "DESCRIBES",
            }
        ],
    }


def main() -> None:
    kernel_version = sys.argv[1]
    doc = build_doc(kernel_version)
    assert doc["packages"][0]["versionInfo"]
    out = "/rootfs/usr/share/spdx/kernel.spdx.json"
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {out} for kernel {kernel_version}")


if __name__ == "__main__":
    main()
