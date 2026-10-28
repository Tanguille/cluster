# Infrastructure Trivy evidence

Snapshot: 2026-10-28, with report ages calculated at 16:49 UTC. This reference
covers the ten infrastructure namespaces below and shared cluster RBAC. The
separate `ai`, `default`, `flux-system`, `kritika`, `media`, `observability`,
`renovate` and `web3` drafts retain their own findings. No deployment, scan Job,
reconciliation, ignore rule or permission removal was performed for this ledger.

## Image findings and coverage

The [image ledger](trivy-infrastructure-images.csv) contains 59 reported digests;
join `image_id` to the [finding ledger](trivy-infrastructure-findings.csv).
There are **1,516** distinct image/package/installed-version/severity findings:
**34 Critical and 1,482 High**. Of these, 1,267 have a fixed package version in
the retained reports. None is marked resolved. These are not unique CVE counts,
and a package fix version is not proof that a supported replacement image contains
the fix. Blank fixed versions mean the scanner supplied none.

Matching uses immutable digests from regular and init container specs or running
container statuses. Controller-to-Pod ownership chains supply runtime digests for
unpinned chart values; container names and configured image references must also
match. Current Pod images and ReplicaSet owners were refreshed through MCP. The
controller inventory includes CronJobs and dormant templates; no infrastructure
Deployment or StatefulSet in this snapshot was scaled to zero. Completed Jobs and
old owners do not justify a current-workload claim, but their retained image
reports are included when their digest exactly matches an image still in use.
Report copies, target copies and shared images are deduplicated. All reported
fixed-version strings are retained, including disagreements between reports.

The [coverage ledger](trivy-infrastructure-coverage.csv) separates configured and
runtime digests and identifies four exact-digest coverage gaps:

- Actions runner controller/listener image 0.15.1.
- Actions runner image 2.338.2.
- Kguardian's BusyBox 1.38.0 init-container runtime digest.
- Kguardian controller 1.17.2 runtime digest.

The BusyBox report exists for a different digest; this review has not established
platform-content equivalence for that pair. It remains unknown rather than being
silently treated as a matching scan. A tag name alone is not an identity match.
The other reported images may also have incomplete scan dimensions; a zero count
is limited to the retained reports. The oldest matching image report is from
October 21, over seven days old. No fresh scan of a proposed replacement exists.

| Namespace | Reported digests | Critical | High | Exact-digest gaps |
| --- | ---: | ---: | ---: | ---: |
| actions-runner-system | 0 | 0 | 0 | 2 |
| cert-manager | 3 | 0 | 1 | 0 |
| database | 8 | 5 | 231 | 0 |
| kopiur-system | 4 | 0 | 73 | 0 |
| kube-system | 15 | 8 | 225 | 0 |
| network | 8 | 7 | 215 | 0 |
| openebs-system | 1 | 5 | 246 | 0 |
| rook-ceph | 9 | 2 | 212 | 0 |
| security | 10 | 7 | 278 | 2 |
| system-upgrade | 1 | 0 | 1 | 0 |

Namespace rows overlap when they use the same image; do not sum them to obtain
the deduplicated totals above. The image ledger records all affected namespaces.
The largest retained concentrations include Postfix, OpenEBS, Ceph CSI, database
sidecars/poolers and the security scanners themselves. Publisher remediation for
images outside the candidate list below remains unverified.

## Candidate changes and remaining evidence

The namespace drafts propose Trivy 0.76.1, database kube-rbac-proxy 0.23.1,
node-problem-detector 1.37.0 and CoreDNS 1.15.0. Their actual image build/package
metadata was read without executing image binaries. The ledgers retain the
pre-change findings because package evidence is narrower than a fresh image scan.

The [Trivy engine proposal](trivy-security.md) records its verified dependency
improvements, remaining candidate findings and why Trivy Operator itself stays on
its current chart and image. Node-problem-detector 1.37.0 still uses Go
1.27.2; a remaining High finding requires 1.27.3. Neither update is a clean scan.

The [Kubernetes patch draft](https://github.com/Tanguille/cluster/pull/6308)
sets Tuppr's target to 1.37.2 while preserving its health gates. Actual API server
metadata confirms Go 1.26.10 meets four current standard-library High fix criteria;
five module findings remain. Other candidate control-plane binaries still need
fresh reports. Merging that separate draft would start an upgrade and requires
owner approval; this ledger changes no upgrade settings.

The [Rook CSI draft](https://github.com/Tanguille/cluster/pull/6310) updates provisioner to 6.4.0 and snapshotter to 8.7.0 via
the native image set. Artifact versions meet 18 of 23 and 24 of 26 existing High
fix criteria respectively; residual Go/x/net findings remain. A native snapshotter
argument preserves the existing disabled group-snapshot behavior. Resizer 2.3.0
is blocked because it removes a feature gate still passed unconditionally by
operator 1.1.0. No storage rollout or disposable fixture test has occurred.

Final primary release checks matched the existing Cilium 1.20.4, etcd-defrag
0.47.0, Spegel 0.8.1, CSI attacher 4.13.0 and node-driver-registrar 2.18.0
versions. The generic-device-plugin `latest` registry digest still equals its
configured immutable pin, and the k8s-gateway registry has no stable version
newer than 1.9.2. Those checks found no newer published replacement; their
reported findings remain open.

Three additional publisher checks have concrete blockers:

- **CrowdSec:** [v1.8.2 source](https://github.com/crowdsecurity/crowdsec/releases/tag/v1.8.2)
  is released, but its image tag returns `MANIFEST_UNKNOWN` from both Docker Hub
  and the official GHCR mirror. Both tag inventories still end at v1.8.1;
  Docker Hub's unprefixed `1.8.2` tag is also absent. Image publication uses a
  separate [manual workflow](https://github.com/crowdsecurity/crowdsec/blob/v1.8.2/.github/workflows/publish-docker-release.yml).
  Chart 0.24.2 has a shared native `image.tag` for agent, AppSec, LAPI and their
  registration init containers, but a source release does not prove the image's
  compiler or dependencies. Keep the 21 deduplicated High findings open until
  a published artifact can be inspected and scanned; no override or rebuild.
- **ExternalDNS:** [v0.24.0](https://github.com/kubernetes-sigs/external-dns/releases/tag/v0.24.0)
  is published, but changes HTTPRoute annotation/template hostname precedence
  over listener fallback. This cluster uses `gateway-httproute` with `policy: sync`,
  so an upgrade can delete previously created listener/wildcard records. The
  enabled `crd` source also needs the new DNSEndpoint/DNSRecord admission schemas
  and existing objects reviewed. Compare intended DNS changes in dry-run mode
  and validate the CRD migration before proposing a pin; no blind image override.
- **Ceph:** the [official release manifest](https://github.com/ceph/ceph/blob/main/doc/releases/releases.yml)
  lists Umbrella 21.2.0 released on October 28. Tentacle 20.2.4 remains the latest
  20.x release, supported until June 1, 2027. A move to 21.x requires supported
  Rook pairing and a storage migration review; it is not a routine security pin.
  Neither the newer major's package fixes nor upgrade safety is established here.

The MCP tools expose existing report readers, not an image scan/recheck operation.
Kguardian supply-chain ingestion is unavailable: its 168-hour
`explain_cve_exposure` query for CVE-2026-31789 returned `found: false` despite
Trivy reports containing that finding on current images. That result supplies
neither reachability evidence nor an absence-of-exposure claim.

## Configuration and RBAC warnings

The [audit ledger](trivy-infrastructure-audits.csv) retains 138 current-owner
configuration resource/check rows and 86 RBAC resource/check rows: 293 failed
checks before grouping repeated checks within each resource. Configuration rows
are High; RBAC includes 59 Critical and 27 High resource/check rows. Report times
range from September 13 to October 28. Where Trivy supplies no update timestamp,
the CSV explicitly uses report creation time. Matching a current owner UID does
not prove the report reflects its latest specification.

Current Role/ClusterRole and binding UIDs were read through MCP. Reports for
deleted owners were excluded. Cluster roles bound exclusively to service accounts
in the eight separately reviewed namespaces are omitted here; shared roles and
built-in roles remain. An unbound role is still a permission definition, not proof
of an actively exploitable grant. The ledger does not publish binding subjects.

- **Cilium, Ceph/CSI and device plugins:** host networking, host paths, privileged
  device/mount access and selected capabilities have functional requirements.
  Preserve these while checking each container against its documented role.
  Removing them solely to clear a warning can break networking or storage.
- **Talos control-plane components and etcd maintenance:** host access belongs to
  their control-plane/maintenance function. Review through the owning Talos or
  maintenance configuration; do not patch generated Pods.
- **Kguardian controller:** host capture access is intentional. Keep `llmBridge`
  enabled and separate capture permissions from report-ingestion prerequisites.
- **Writable roots and default contexts:** Dragonfly, gateways, CrowdSec, OpenEBS,
  CoreDNS, metrics/descheduler and storage sidecars require an effective chart
  render and startup/steady-state write-path review. Explicit container contexts
  and narrowly scoped writable mounts are follow-up candidates, not verified safe
  blanket changes. Preserve startup ownership and device requirements.
- **RBAC:** certificate/secret management, storage provisioning, backup movers,
  database controllers and gateway reconciliation legitimately manipulate some
  sensitive resources. Review exact resources, verbs, names and bindings against
  those functions. Wildcards, `nodes/proxy`, `pods/exec`, RBAC writes and webhook
  writes merit separate scope checks; no controller permissions were stripped.
  Preserve Kubernetes built-in role definitions and review their bindings instead.

No configuration or RBAC warning is marked resolved by this ledger. A source or
package update does not remediate a separate context/permission finding. Fresh
scans, compatible chart renders, owner-approved rollout checks and application
health are required before claiming remediation.

## Local verification

CSV checks verify unique finding keys, exact source digest matches, preservation
of every Critical/High package finding and reported fix string, per-image totals,
coverage joins and current-owner audit membership. Public output contains no raw
capture addresses, credentials, binding subjects or private source paths.
Static validation does not authorize an infrastructure rollout.

## Namespace drafts

All remain drafts. The eight kguardian PRs also contain enforcing network policies;
those require at least seven days of usable audit evidence and explicit owner
approval. The five image/upgrade drafts require their documented compatibility,
fresh-scan and rollout checks. No PR is deployment approval.

| Namespace | Draft | Scope |
| --- | --- | --- |
| flux-system | [#6288](https://github.com/Tanguille/cluster/pull/6288) | Network/context draft and Trivy coverage |
| renovate | [#6289](https://github.com/Tanguille/cluster/pull/6289) | Network/context draft and unresolved dependency |
| kritika | [#6290](https://github.com/Tanguille/cluster/pull/6290) | Network/context draft and unresolved dependency |
| media | [#6291](https://github.com/Tanguille/cluster/pull/6291) | Network/context draft, FileFlows init and findings |
| web3 | [#6292](https://github.com/Tanguille/cluster/pull/6292) | Network/context draft, P2Pool root and miner findings |
| ai | [#6293](https://github.com/Tanguille/cluster/pull/6293) | Network/context draft, Flux MCP and curl images |
| default | [#6294](https://github.com/Tanguille/cluster/pull/6294) | Network/context draft, notify-push and findings |
| observability | [#6295](https://github.com/Tanguille/cluster/pull/6295) | Network/context draft, VM images and exporters |
| security | [#6305](https://github.com/Tanguille/cluster/pull/6305) | Trivy engine and infrastructure evidence |
| database | [#6306](https://github.com/Tanguille/cluster/pull/6306) | Dragonfly metrics proxy image |
| kube-system | [#6307](https://github.com/Tanguille/cluster/pull/6307) | CoreDNS and Node Problem Detector images |
| system-upgrade | [#6308](https://github.com/Tanguille/cluster/pull/6308) | Kubernetes patch target |
| rook-ceph | [#6310](https://github.com/Tanguille/cluster/pull/6310) | CSI provisioner/snapshotter images |
