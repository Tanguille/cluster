# Rook CSI image remediation

The October 28, 2026 review matched existing Trivy reports to the CSI sidecars
and inspected candidate image metadata without executing the binaries. The
changes use Rook chart 1.21.1's native `csi.provisioner` and `csi.snapshotter`
values. They populate the image-set ConfigMap already referenced by the
ceph-csi-drivers chart; both charts and the operators remain pinned.

| Sidecar | Current image | Proposed image | Shipped Go version | Existing High fix thresholds met |
| --- | --- | --- | --- | --- |
| Provisioner | v6.3.0 | v6.4.0 | 1.26.3 → 1.26.6 | 18 of 23 |
| Snapshotter | v8.6.0 | v8.7.0 | 1.26.3 → 1.26.9 | 24 of 26 |

These counts deduplicate vulnerability/package pairs across RBD and CephFS;
they compare shipped versions with existing report criteria, not new scans.
Both candidates embed `golang.org/x/net` 0.59.0, `golang.org/x/crypto` 0.57.0,
`golang.org/x/text` 0.42.0 and gRPC 1.84.0. Registry index pins were resolved to
the exact Linux/amd64 manifests inspected.

## Compatibility

- [Provisioner v6.4.0](https://github.com/kubernetes-csi/external-provisioner/releases/tag/v6.4.0)
  requires Kubernetes 1.34 or later; the observed cluster is 1.37.1. Its source
  retains the operator's arguments, including `Topology=false`. Existing
  controller RBAC supplies the required API access.
- [Snapshotter v8.7.0](https://github.com/kubernetes-csi/external-snapshotter/releases/tag/v8.7.0)
  enables group snapshots by default. The native controller
  `containerExtraArgs` explicitly retains `CSIVolumeGroupSnapshot=false` for
  both drivers, preserving their existing `volumeSnapshot` behavior. The
  installed group-snapshot CRDs already serve v1 and existing RBAC covers them,
  but this update does not enable the feature. Operator 1.1.0 inherits these
  default arguments and appends them to the snapshotter command; neither
  driver supplies conflicting arguments.
- [Resizer v2.3.0](https://github.com/kubernetes-csi/external-resizer/blob/v2.3.0/CHANGELOG/CHANGELOG-2.3.md)
  is deferred: it removes `RecoverVolumeExpansionFailure`, while
  the pinned operator unconditionally passes that feature gate. The candidate
  exits on the unknown gate. Updating its image alone would break resizing;
  the operator must first support the new command contract. The existing
  resizer's 24 High findings remain open.

## Remaining findings and rollout gates

Snapshotter retains two known High vulnerability/package findings:
`CVE-2026-78669` in `golang.org/x/net` and `CVE-2026-111943` in Go. Provisioner
retains those plus Go findings `CVE-2026-78667`, `CVE-2026-78669` and
`CVE-2026-97031`. Other Rook/Ceph images are unchanged. A newer dependency
version does not establish that the namespace is vulnerability free.

Before/after renders differed only in the two image-set entries and the
explicit snapshotter feature argument. CSI RBAC, storage classes, driver
identities, two controller replicas, mounts, contexts and resources were
unchanged. The repository validator, Markdown lint and independent review passed.
After owner-approved rollout, verify both controller deployments use the exact
images and preserve single-volume snapshot behavior. Exercise PVC provisioning,
snapshot/restore and expansion using disposable fixtures, then obtain fresh
Trivy reports for the deployed digests. No live update or storage fixture has
been performed in this draft.
