# Private Family Blog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare a GitOps deployment of the preserved family blog for LAN/VPN-only browsing, without an additional login or changes to original family content.

**Architecture:** A non-root NGINX Deployment serves only verified offline content from a dedicated read-only-mounted PVC. A temporary GitOps-managed importer receives the private archive during an explicitly approved bootstrap; the web Deployment and route remain disabled until content and network checks pass.

**Tech Stack:** Flux, shared app-template, Gateway API/envoy-internal, ceph-block PVC, NGINX unprivileged, Python standard-library importer.

**Spec:** `docs/superpowers/specs/2026-11-08-family-blog-design.md`

## Global Constraints

- App `family-blog` in `default`; HTTPS `family-blog.${SECRET_DOMAIN}` through `envoy-internal` only.
- No extra login, public route, tunnel, public discovery entry, or family content in Git/images.
- Serve only `offline/` from the verified private ZIP. Preserve the ZIP and original photos.
- Every cluster resource is GitOps-managed. Ask before push, live reconciliation, import, or restart.
- Use existing versions confirmed upstream: `nginxinc/nginx-unprivileged:1.30.6-alpine-slim` and temporary `python:3.14.10-alpine`; shared `app-template` chartRef.
- Read nearest AGENTS files; preserve unrelated untracked files. Work on an isolated branch/worktree after approval.
- No implementation-specific changes to existing namespace-wide network policies.

## Review Focus

1. Malformed, truncated, or dishonest ZIP: refuse without publishing readiness or modifying existing content.
2. Existing PVC data: refuse import, including repeat runs; never overwrite or empty a volume.
3. Half-populated content/RWO attachment: no serving during bootstrap; never overlap importer and serving Pods.
4. Public wildcard tunnel, NodePorts, or discovery: internal route name alone does not prove privacy.
5. Offline links, CSP, missing images: preserve navigation and documented placeholders without automatic Internet requests.

---

### Task 1: Tested, bounded archive importer

**Files:**
- Create `kubernetes/apps/default/family-blog/seed/import.py`.
- Create `scripts/test_family_blog_seed.py`.

**Interfaces:** `import_archive(stream, expected_sha256, destination: pathlib.Path) -> dict` verifies and populates an empty PVC root; returns file count and extracted bytes. CLI creates `/tmp/archive.fifo`, reads a 64-hex-character SHA256 line followed by ZIP bytes, and calls the function for `/data`. Task 2 mounts the script and volume; no network downloads or credentials.

- [ ] Write synthetic-ZIP unit tests first: valid import yields only `site/index.html`, expected assets and `.ready`; source bytes match fixture hashes. Missing/nonempty index, missing hashes, wrong ZIP/file hashes, unlisted/duplicate files, truncated input, traversal/absolute paths, symlinks and nonregular members must raise with `.ready` absent. Existing `site`, `.ready`, unrelated files, or abandoned staging must remain unchanged on refusal; allow only filesystem-created `lost+found` in a fresh destination.
- [ ] Run `python3 -m unittest discover -s scripts -p 'test_family_blog_seed.py' -v`; confirm the new tests fail before implementation.
- [ ] Implement using only standard libraries. Limit compressed input to 256MiB, total expanded offline bytes to 512MiB, files to 10,000 and checksum metadata to 8MiB; test each limit with injectable smaller test bounds. Verify ZIP checksum before extraction, then require exact offline-entry agreement with `metadata/SHA256SUMS.txt`. Extract only regular `offline/` files into a new staging directory, verify each hash, atomically rename to `site`, and write `.ready` last. Do not trust archive permissions or overwrite originals. Clean only staging created by the current invocation; preserve actionable failure output.
- [ ] Run the full importer test file again; require all tests pass and no nonstandard dependency.

### Task 2: Bootstrap manifests and operator runbook

**Files:**
- Modify `kubernetes/apps/default/kustomization.yaml` to register `./family-blog/ks.yaml`.
- Create `kubernetes/apps/default/family-blog/ks.yaml`.
- Create `kubernetes/apps/default/family-blog/app/{kustomization.yaml,helmrelease.yaml,pvc.yaml}`.
- Create `kubernetes/apps/default/family-blog/app/config/nginx.conf`.
- Create `kubernetes/apps/default/family-blog/seed/{kustomization.yaml,job.yaml}`.
- Create `docs/family-blog.md`.

**Interfaces:** Task 1 script is mounted by ConfigMap into a one-shot seed Job with PVC RW at `/data`. NGINX mounts the same PVC RO at `/srv`, serving `/srv/site`. The app Kustomization includes the seed Component only during bootstrap. A later approved GitOps change removes it and activates the server/route.

- [ ] Model manifests on `default/it-tools` and nearby ConfigMap generators. Declare a standalone 1Gi RWO `ceph-block` PVC with `kustomize.toolkit.fluxcd.io/prune: disabled`, no automatic Restore. Explain rebuild-only recovery from the private verified ZIP as an intentional kopiur exception for derived static data.
- [ ] Implement seed Job: UID/GID/fsGroup101, seccomp RuntimeDefault, non-root, dropped capabilities, no token mount, read-only root and writable bounded `/tmp`; one replica, `backoffLimit: 0`, `activeDeadlineSeconds: 86400`, no TTL/controller recreation loop. Mount script RO; no Service or external access requirement. Use ConfigMap generator conventions; no source archive URI/hash/name in public manifests.
- [ ] Implement NGINX Deployment with `Recreate`, bootstrap replicas0, route disabled; runtime UID/GID/fsGroup101, same hardening, read-only PVC and root, writable16Mi `/tmp`. Execute `nginx -g 'daemon off;'` directly. Resources: requests5m/32Mi, limits200m/128Mi. Service ClusterIP8080. ConfigMap reload follows repo convention.
- [ ] Implement NGINX: root `/srv/site`, readiness gate `/srv/.ready`, `/healthz` constant200, index.html, no directory listing, dotfiles/PHP denied, GET/HEAD only, no handlers/proxies/access log. Set MIME types, temporary paths under `/tmp`, and headers `nosniff`, `no-referrer`, `noindex, nofollow, noarchive`. CSP permits only local/data images and local/inline styles, disallowing scripts, connections, forms, objects and frames. Test actual archive compatibility, not just config syntax.
- [ ] Route only to `envoy-internal` HTTPS listener; explicitly disable Gatus and homepage discovery. No external-DNS or public-share annotations. Startup/liveness probe `/healthz`; readiness `/index.html`.
- [ ] Write runbook for obtaining original ZIP and expected checksum from private Nextcloud, verifying locally, waiting for the seed FIFO, streaming checksum+ZIP through approved `kubectl exec`, and checking Job success/hash inventory. Recovery uses a fresh empty PVC and the same bootstrap, never overwrite. Document activation through Git after seed completion/volume release, and rollback disabling route/server while retaining PVC. Preserve the backup outside the served document root.
- [ ] Document required privacy checks: live internal gateway address, WAN/IPv6/UPnP/NodePort forwarding and wildcard tunnel routing, public DNS, Gatus/homepage exclusions. A public wildcard may resolve; it must not serve this site. No authenticated-content claim: trusted LAN/VPN clients can view it. If firewall/off-network evidence is unavailable, stop before claiming isolation and request the missing check.

### Task 3: Reconcile validation and request deployment approval

**Files:** task1 tests and task2 manifests/runbook; validation artifacts stay outside public Git.

- [ ] Run importer tests and locally render both bootstrap and activation variants (scratch overlay, no unreviewed source edits). Assert bootstrap route absent/replicas0 and seed present; steady-state route internal HTTPS only/replicas1 and seed absent. Check effective PVC RO mount, labels, generated config references, service/probe ports and security settings.
- [ ] Test exact NGINX image/config against synthetic fixtures as UID101 with RO root and dropped capabilities: empty site503, health200, populated index/image200, existing directory without index403, PHP/dotfiles/mutating methods rejected. Use a local container runtime if available; report unavailable runtime evidence rather than deploying to test without approval.
- [ ] Use a temporary local web server with actual archive/config where feasible to test root, first/middle/last posts, images and guestbook; confirm no automatic external requests, scripts or submits. Check `.ready` and non-served raw/ZIP files cannot be fetched.
- [ ] Parent runs `git diff --check` and `bash .agents/skills/pr-review/scripts/validate-pr.sh` once after reconciling writers; review exact staged scope and a focused privacy/importer review. Document tool/environment limitations honestly.
- [ ] Present files, validation evidence and the two-phase rollout. Ask before committing if required by app workflow, and always before push/live reconciliation/import. Do not deploy in this implementation pass.
- [ ] After separate approvals, verify live imported checksums, LAN/VPN and public-negative paths, and persistence across an approved restart/reschedule; record unavailable external-client tests rather than asserting success.
