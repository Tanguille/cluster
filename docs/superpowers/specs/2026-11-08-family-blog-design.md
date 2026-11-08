# Private family-blog hosting

Status: approved by the user on 2026-11-08; implementation prepared in an isolated
worktree. Deployment, reconciliation and live import require separate approval.

## Intent and agreed choices

Let the family browse the preserved blog privately without depending on Bloggen.be.
The user chose home-network/VPN access without an additional login, and a dedicated
private volume rather than a container image containing family content.

## Approaches

- **Selected: static server plus dedicated PVC.** Keeps family material out of Git
  and registries; requires a controlled initial content copy.
- **Alternative: private image containing the site.** Immutable and reproducible,
  but adds private image publishing and a build workflow with no present benefit.

## Proposed small deployment

- App `family-blog` in `default`, following existing app-template/Flux conventions.
- HTTPS hostname `family-blog.${SECRET_DOMAIN}`, attached only to `envoy-internal`.
- Lightweight non-root static server with a read-only document-root mount,
  resource limits, health probes, and directory listings disabled.
- Dedicated PVC sized from the extracted site with modest headroom. Match existing
  storage conventions; do not mount Nextcloud homes or the broad NAS share.
- Populate the volume from the verified archive in a controlled one-time import.
  Cluster resources are declared through GitOps; live reconciliation and the
  content import require separate approval. Do not overwrite an existing volume.
- Serve only the archive's `offline/` contents, with `index.html` at the web root.
  No raw captures, ZIP, credentials, execution of PHP, or submission handlers.
- Preserve the Nextcloud ZIP and checksums unchanged as the recovery source.
  Keep family content outside repository commits and public container images.
  Document recovery by reimporting the verified archive rather than depending
  on an unpublished local staging directory.

## Privacy and limitations

No public-gateway route, tunnel, public share, or public service discovery entry.
Verify actual gateway/firewall/NAT and DNS behavior: the name `envoy-internal`
is not proof of isolation. No new WAN access or VPN reconfiguration.

This is a read-only historical website: comments and guestbook are readable, not
interactive. Existing missing-image placeholders and reconstruction limitations
remain unchanged. Public-site takedown and Internet Archive exclusion are separate
tasks; private hosting does not remove their copies.

## Acceptance and rollout

1. Validate rendered Kubernetes manifests with the repository validation script.
2. Verify archive checksum and imported file checksums; preserve original backup.
3. Confirm HTTPS, local navigation, representative posts, images and guestbook.
4. Confirm no active scripts or automatic requests to the original website.
5. Confirm LAN access and VPN access where a real test client is available.
6. Confirm public ingress does not serve the hostname or image paths; inspect
   WAN forwarding and public DNS, and report any unavailable off-network test.
7. Restart/reschedule the workload and verify the persistent content remains.

Prepare a focused GitOps change first. Ask before pushing, live reconciliation,
or importing content. No existing family photos or archive files are changed.
