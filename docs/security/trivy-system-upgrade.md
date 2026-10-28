# Kubernetes patch security draft

Propose Kubernetes **1.37.1 → 1.37.2** through Tuppr's existing
`KubernetesUpgrade.spec.kubernetes.version`. This is a draft on
`harden/trivy-system-upgrade`; no upgrade, apply, reconciliation or scan was run.
Merging into the reconciled branch would authorize Tuppr to start the upgrade,
so owner approval is required before merge.

## Evidence

The official [stable release](https://github.com/kubernetes/kubernetes/releases/tag/v1.37.2)
was published on 2026-10-28. Its [changelog](https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.37.md#v1372)
documents Go 1.26.10 and patch-level bug fixes, with no dependency-module changes.
This retains the existing Kubernetes minor version. The Siderolabs kubelet
`v1.37.2` artifact exists at digest
`sha256:d80d3843530cfae376a0814f73bcc4ac70e32bc157de777b38a90f33b82d1111`.

The actual API server image was inspected as data, without executing its binary:

- Image index: `registry.k8s.io/kube-apiserver:v1.37.2` at
  `sha256:3e563afb5cb5b72a10bac7012698dd09f5a42bde7d22de8d7d3c12e654df86b4`.
- Inspected linux/amd64 manifest:
  `sha256:f3916d65eddfe52b6d21ff4a17abdf4c52589c8ef62ec7e0c17204f276cd8db6`.
- Go build metadata confirms **Go 1.26.10**, replacing the current reported 1.26.8.

That compiler version meets the reported fixes for four High findings:
CVE-2026-111943, CVE-2026-78667, CVE-2026-78669 and CVE-2026-97031 in `stdlib`.
This is verified package-version evidence, not a fresh scan or a runtime result.
Current control-plane reports have nine distinct High package findings per image
after repeated target copies are deduplicated.

## Remaining findings

The candidate API server still contains five reported High package findings:

| Finding | Package | Candidate | Reported fix |
| --- | --- | --- | --- |
| CVE-2026-56854 | golang.org/x/crypto | 0.54.0 | 0.55.0 |
| CVE-2026-78669 | golang.org/x/net | 0.57.0 | 0.60.0 |
| CVE-2026-56851 | golang.org/x/text | 0.40.0 | 0.41.0 |
| CVE-2026-84304 | google.golang.org/grpc | 1.82.1 | 1.83.1 |
| CVE-2026-84445 | google.golang.org/grpc | 1.82.1 | 1.82.2 or 1.83.2 |

The current kubectl 1.37.2 report has a smaller dependency set and cannot predict
API server findings. No clean-scan claim follows from it. Preserve the remaining
findings for a later upstream patch; no suppression or custom Kubernetes rebuild
is introduced. Scheduler, controller-manager and kubelet candidate images still
need fresh reports; only the API server binary was inspected here.

## Validation and rollout gate

The manifest change is limited to the Kubernetes target version. Talos versions,
talosctl image selection, node/database readiness checks and backup/restore gates
remain unchanged. No Talos secrets or workstation tool versions are modified.

Local checks passed: `mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh`
(Flux/Helm rendering, shellcheck, secret guardrails and 25 regression tests), a
targeted Kustomize render, Markdown lint and `git diff --check`. Source/render
assertions verified the single version-field change and preserved health gates.

Before an approved rollout, refresh cluster health and upgrade compatibility,
confirm backup/restore operations are idle, and verify the preserved health gates.
After approval, follow Tuppr's progress and check API availability, scheduler and
controller health, all node kubelet versions, Cilium networking, DNS, storage and
application readiness. Collect fresh Trivy reports for all new control-plane
digests and retain any remaining findings. Static checks do not prove rollout
success or authorize enforcement.
