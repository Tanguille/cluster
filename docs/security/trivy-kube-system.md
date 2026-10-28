# kube-system image remediation

The October 28, 2026 review matched existing Trivy reports to the running
workloads and inspected the proposed upstream images without executing them.
These are image updates only.

| Workload | Reported image | Existing report | Proposed image |
| --- | --- | --- | --- |
| CoreDNS | 1.14.7 | 0 Critical / 22 High, October 27 | 1.15.0 |
| Node Problem Detector | v1.35.1 | 10 Critical / 175 High, October 27 | v1.37.0 |

Report totals can include the same vulnerability in several embedded binaries;
they are not counts of distinct flaws or a scan of the proposed images.

## Evidence and compatibility

- [CoreDNS 1.15.0](https://github.com/coredns/coredns/releases/tag/v1.15.0)
  ships a binary built with Go 1.27.3, `golang.org/x/net` 0.60.0 and gRPC 1.84.0.
  Its plugin list retains every configured plugin, including `autopath` and
  `template`. Chart 1.49.0 remains pinned. Its native `image.tag` supports the
  digest pin and sanitizes the derived version label; the Corefile, selectors,
  two replicas, disruption budget and scheduling remain unchanged.
- [Node Problem Detector v1.37.0](https://github.com/kubernetes/node-problem-detector/releases/tag/v1.37.0)
  ships the main binary and helpers built with Go 1.27.2. The main binary embeds
  `golang.org/x/net` 0.61.0, `golang.org/x/crypto` 0.58.0,
  `golang.org/x/text` 0.43.0 and gRPC 1.83.2. The image contains updated Debian
  GnuTLS and Perl packages. Its telemetry migration preserves Prometheus metric
  names. Chart 2.4.2 and both Talos-compatible kmsg monitors remain unchanged,
  including their command, RBAC, mounts, contexts and resource settings.
- Both HelmReleases pin the release's registry index digest. The inspected
  Linux/amd64 manifest matches that index's Linux/amd64 descriptor.
- Pinned-chart before/after renders differed only in container images and
  CoreDNS's derived metadata version label, which passed the Kubernetes label
  length and character checks.

## Remaining findings and rollout gates

Node Problem Detector still embeds Go 1.27.2: the reported High
`CVE-2026-111943` requires Go 1.27.3 or 1.26.10 and remains unresolved. Some OS
findings also remain, including unchanged zlib packages without a reported fix.
CoreDNS embeds a pseudo-version for its main module; existing findings against
that package need release-to-source mapping and a fresh scan before closure.
Other kube-system images and configuration findings are outside these two
updates.

After owner-approved rollout, verify cluster DNS (including forwarding and
negative responses), CoreDNS readiness, Node Problem Detector node-condition
reporting and metrics, then obtain fresh Trivy reports for the exact deployed
digests.
