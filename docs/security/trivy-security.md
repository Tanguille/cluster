# Trivy engine security update

Snapshot: 2026-10-28. Draft only; no apply, reconciliation, scan Job, or rollout
was performed. The [infrastructure ledger](trivy-infrastructure.md) retains the
reported findings and coverage gaps across the remaining namespaces.

## Proposed change

Pin the shared Trivy scan-client/server image to 0.76.1 using the existing chart's
native `trivy.image.tag`. Keep chart 0.32.1 and operator 0.30.1, the current scanner
concurrency, resource settings, namespace exclusions and report retention.
The chart renders the same image for its built-in server and scanner configuration.
No kguardian setting changes; `llmBridge` remains enabled.

The current 0.69.3 image has 3 Critical and 113 High report entries. Those are the
pre-change findings, not a prediction of the new scan count. The pinned replacement's
amd64 build metadata and APK package database were read as data, without executing
image binaries. They show Go 1.27.3, OpenSSL 3.5.8, gRPC 1.84.0, x/net 0.60.0,
x/crypto 0.57.0, x/mod 0.41.0, x/text 0.42.0 and Trivy 0.76.1. These meet the
reported fixes for those packages, including the three Critical entries and
Trivy's own 0.71.0/0.71.1 fixes. The [release notes](https://github.com/aquasecurity/trivy/releases/tag/v0.76.1)
also document the Go update and CA-trust mitigation.

## Compatibility evidence

Read-only source checks of operator 0.30.1 and Trivy 0.76.1 found the generated
scan flags retained, identical vulnerability structs, JSON decoding tolerant of
added fields and DB schema 2 on both sides. The new server retains `/healthz`,
its scanner/cache RPC routes and standard Docker keychain authentication. See
the [operator plugin](https://github.com/aquasecurity/trivy-operator/tree/v0.30.1/pkg/plugins/trivy)
and [server source](https://github.com/aquasecurity/trivy/blob/v0.76.1/pkg/rpc/server/listen.go).
Local rendering changes exactly the
scanner image configuration and the built-in server image. This does not prove
runtime startup, cold-cache behavior or compatibility of in-flight older scan Jobs.

The operator embeds its own Trivy 0.69.3 library for configuration/RBAC checks;
changing the external image does not update that library. Also, this old operator
extracts `scanner.version` from the last image-reference colon: a digest pin makes
that metadata field contain the digest rather than a semantic version. Findings
remain available, but consumers requiring a version must use the configured tag
until the operator is updated. No extra parser or manifest workaround is added.

## Remaining findings and limits

This update is not a clean vulnerability scan. In particular, the candidate still
contains brotli-libs 1.2.0-r1 (reported fix 1.2.1-r0) and libcurl 8.22.0-r0
(reported fix 8.23.0-r0 for CVE-2026-92392). Its go-jose/v4 4.1.5 is below the
4.1.6 fix reported for CVE-2026-116587 in another current image. Retain these as
follow-ups for the next publisher build; do not silence them with ignore rules.

The latest Trivy Operator source release is 0.35.0, but its chart 0.37.0 was not
published at the configured OCI chart source during this check. The latest
published chart remains 0.32.1. An operator image override alone would bypass
unverified CRD/RBAC changes, and the 0.35.0 artifact still uses Go 1.27.0 and
x/net 0.59.0. The operator's own findings therefore remain open.

Other security images, including kguardian's enabled LLM bridge, also retain
reported findings without a verified patched publisher replacement. Namespace
exclusions leave persistent AI images without current scans. Restoring that
coverage requires an explicit scan-load review because report churn previously
caused storage pressure. Absence of a report is unknown, not a pass.

The MCP `explain_cve_exposure` query for CVE-2026-31789 over 168 hours returned
`found: false` even though direct Trivy reports list it on current digests. This
confirms the missing kguardian ingestion; it cannot establish reachability or
absence of exposure for this known finding.

The discovered MCP tools can read existing reports; they do not expose a fresh
image-scan operation. A fresh candidate scan and an approved rollout must verify
scanner startup, report parsing, DB/cache behavior, queue throughput and the
remaining findings. Do not merge or deploy merely because static rendering passes.

Validation passed: required full repository validator (Flux/Helm rendering,
shellcheck, secret guardrails and 25 regression tests), Markdown lint and exact
before/after pinned-chart render comparison. Independent source review found no
static compatibility blocker; the runtime gates above remain open.
