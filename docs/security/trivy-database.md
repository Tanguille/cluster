# database image remediation

The October 28, 2026 Trivy report for Dragonfly operator's `kube-rbac-proxy`
v0.20.2 sidecar contains 2 Critical and 48 High findings. The proposed change
updates only that sidecar to upstream v0.23.1 through the pinned chart's native
`rbacProxy.image.repository` and `rbacProxy.image.tag` values. The repository
stays explicit so chart default changes cannot separate the digest from its
registry. The Dragonfly operator and chart remain v1.7.1.

## Evidence and compatibility

- [kube-rbac-proxy v0.23.1](https://github.com/brancz/kube-rbac-proxy/releases/tag/v0.23.1)
  ships a binary built with Go 1.27.3, `golang.org/x/net` 0.61.0 and gRPC 1.83.2,
  replacing older components identified by the existing report. These versions
  came from static build metadata in the candidate image, not its `go.mod`
  minimum or execution of the image.
- The digest-pinned registry index resolves to the Linux/amd64 manifest whose
  binary was inspected. Chart 1.7.1 renders the native override directly into
  the proxy container image.
- The new proxy retains the chart's secure-listen and upstream arguments.
  Its [deprecated-flag compatibility](https://github.com/brancz/kube-rbac-proxy/blob/v0.23.1/cmd/kube-rbac-proxy/app/options/deprecated.go)
  accepts the existing `--logtostderr=true` argument with a warning. The
  listener, upstream, authorization settings, RBAC, service, contexts, resources
  and manager image remain unchanged.

## Remaining findings and rollout gates

This is a bounded dependency update, not evidence that all 50 reported findings
have cleared. No fresh candidate scan was available. The operator manager's
existing 3 High findings and findings in other database workloads remain
separate; no suppressions or startup package upgrades are introduced.

Pinned-chart before/after renders differed only in the proxy container image.
The repository validator and Markdown lint passed.
Owner approval is required before merging this draft. Merging to main allows
the unsuspended `dragonfly` Flux Kustomization to roll out the change on its
next reconciliation; there is no additional deployment approval gate. After
that rollout, verify operator readiness and Dragonfly reconciliation,
authenticated metrics access and rejection of unauthorized access, then record
a fresh Trivy report for the deployed digest. As of the October 28 review,
deployment and these runtime checks remain unverified.
