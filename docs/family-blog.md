# Private family blog

Read-only historical website at `https://family-blog.${SECRET_DOMAIN}` for the
home network and VPN, **without a login**. Comments and guestbook are preserved
pages, not submission forms. Missing-image placeholders remain missing images.
Hosting this copy does not remove copies on the original site or Internet Archive.
Historical form controls may remain visible, but CSP prevents submission. A legacy
external archive-toolbar badge is also blocked; do not weaken CSP to load it.

## Safety and layout

- NGINX serves only `/srv/site`, the verified archive's `offline/` tree. The PVC
  root is mounted read-only at `/srv`; ZIP, raw captures and metadata are not served.
- The dedicated `family-blog` PVC is 1Gi, RWO, `ceph-block`. It is protected from
  Flux pruning, **not** from manual deletion or storage failure. The storage
  class's reclaim policy is Delete.
- This derived copy intentionally does not use automatic Kopiur restore: the
  unchanged private ZIP and its checksum in Nextcloud are the recovery source.
  Keep those and original photos intact. Do not mount Nextcloud homes here.
- No family content, archive names/checksums, credentials or private recovery URLs
  belong in Git, public logs, images or registries.
- Initial manifests are **bootstrap only**: server replicas `0`, internal HTTPS
  route configured, temporary seed Job enabled. The route has no backend Pods
  until the server is started. Committing configuration does not approve importing
  data or activating the website.

Use GitOps for every resource change. Obtain separate approval before committing,
pushing, reconciling, importing, restarting, or deleting resources. Do not use
`kubectl apply`, `run`, `scale`, or ad hoc Jobs to bypass those gates.

## 1. Check the privacy boundary before activation

The name `envoy-internal` is not an access control. Record these checks privately:

1. Inspect live Gateway/Service addresses and HTTP, HTTPS, HTTP3 and NodePort
   listeners. Check WAN NAT/port forwards, IPv6 ingress rules, UPnP mappings and
   any reverse proxy: none may forward public traffic to the internal gateway or
   its NodePorts. Do not assume a private load-balancer address proves this.
2. Inspect Cloudflared wildcard routing. Its public wildcard must terminate at
   the external gateway, with no route for this website. Inspect other tunnels
   and gateway routes for alternate paths.
3. Inspect public DNS A/AAAA/CNAME records. An existing wildcard can resolve this
   hostname; DNS absence alone is neither required nor proof of privacy.
4. Confirm the rendered route attaches **only** to `envoy-internal` in `network`,
   listener `https`. Gatus and homepage discovery must both be explicitly
   disabled; do not add public status checks, homepage tiles or external-DNS
   annotations for this site.
5. Plan LAN, VPN and genuinely off-network tests for both a page and an image.
   Testing the external gateway from the LAN is useful but does not prove WAN
   isolation. If router/firewall or off-network evidence is unavailable, stop
   before activation and request that check; do not claim the site is private.

The namespace ingress allowlist also permits trusted cluster workloads. This is
network-private hosting, not isolation from cluster administrators or trusted
workloads. An additional additive NetworkPolicy would not narrow that allowlist.

## 2. Bootstrap and import (separate approval required)

1. Obtain the preserved ZIP **and its independently recorded expected SHA256**
   from the owner's private Nextcloud backup/recovery record. Download to a
   private local directory outside this repository. Do not rely on the original
   capture machine's temporary staging directory or publish a Nextcloud share.
2. Review and deploy the bootstrap GitOps change after approval. Confirm the
   server has no Pods and the family-blog HTTPRoute attaches only to the internal
   HTTPS listener. The seed Job must be the only writer to the empty claim. Do not
   overlap seed and server Pods: this is RWO storage.
3. Set local shell variables (do not paste private values into Git). Run in Bash:

   ```bash
   set -euo pipefail
   read -r -p 'Private ZIP path: ' ZIP
   read -r -p 'Expected SHA256 from recovery record: ' EXPECTED_SHA
   printf '%s  %s\n' "$EXPECTED_SHA" "$ZIP" | sha256sum -c -
   ```

4. Confirm the seed Pod is running and its FIFO exists. Use the repository-pinned
   tools via `mise exec` (commands below are read-only until the stream step):

   ```bash
   mise exec -- kubectl -n default get job family-blog-seed
   mise exec -- kubectl -n default wait --for=condition=Ready pod \
     -l job-name=family-blog-seed --timeout=2m
   mise exec -- kubectl -n default exec job/family-blog-seed -c seed -- \
     sh -c 'test -p /tmp/archive.fifo'
   ```

   If the FIFO check fails, inspect the Pod and logs before proceeding. The Job
   has a 24-hour deadline and no retry or TTL recreation loop. Do not stream into
   an expired, completed or failed Job, and do not start competing writers.

5. **Only after import approval**, send the checksum line and original ZIP:

   ```bash
   { printf '%s\n' "$EXPECTED_SHA"; cat -- "$ZIP"; } |
     mise exec -- kubectl -n default exec -i job/family-blog-seed -c seed -- \
       sh -c 'cat > /tmp/archive.fifo'
   mise exec -- kubectl -n default wait --for=condition=complete \
     job/family-blog-seed --timeout=10m
   mise exec -- kubectl -n default logs job/family-blog-seed -c seed
   ```

   A successful pipe is not proof of import: require the Job to complete and the
   importer log to report success. The importer verifies the whole ZIP before
   extraction, then exact `offline/` inventory and every file hash. It publishes
   `site` atomically and writes `.ready` last, outside the document root. The full
   manifest is archive-root-relative; its raw/metadata entries are never extracted.
   Compare the logged file count and bytes with the private recovery validation
   record. A completed Job cannot accept further exec commands.

On timeout/failure, inspect Job/Pod status and logs; do not enable serving. An
interrupted process may leave staging, and existing `site`, `.ready`, unrelated
files or abandoned staging are deliberately refused on retry. Never delete them
automatically. Inspect the state privately and use an approved recovery plan;
Job recreation/removal is a reviewed GitOps change, with deletion approval.

## 3. Activate in two ordered GitOps changes

Only after successful import and the privacy checks:

1. Remove `../seed` from `app/kustomization.yaml` components, keeping server
   replicas `0`. Approve/push/reconcile that change and confirm the seed Job and
   Pods are gone and the volume is released.

   Read-only checks (Job/Pod commands must print nothing; attachment list must
   be `[]`). Recheck if pruning or CSI detachment is still in progress; do not
   delete a VolumeAttachment to force this checkpoint:

   ```bash
   mise exec -- kubectl -n default get job family-blog-seed --ignore-not-found -o name
   mise exec -- kubectl -n default get pods -l job-name=family-blog-seed -o name
   PV=$(mise exec -- kubectl -n default get pvc family-blog -o jsonpath='{.spec.volumeName}')
   test -n "$PV"
   mise exec -- kubectl get volumeattachments.storage.k8s.io -o json |
     mise exec -- jq --arg pv "$PV" \
       '[.items[] | select(.spec.source.persistentVolumeName == $pv) |
         {name: .metadata.name, attached: .status.attached}]'
   ```

2. Set the family-blog controller's `replicas` to `1` in `app/helmrelease.yaml`.
   The internal route is already configured; no route change is needed.
   Render/review before approval and rollout.
   Never switch to an external gateway to work around DNS or connectivity.

Readiness requests `/index.html` and requires real content plus `/srv/.ready`.
Startup/liveness use `/healthz`, which does not establish content readiness.
Before closing the rollout:

- Test HTTPS from LAN and VPN, root/index, first/middle/last posts, local images,
  navigation and historical guestbook. Preserve original appearance and content.
- In browser developer tools, check for scripts, form submissions or automatic
  external requests. CSP blocks active content and remote resources; manually
  following an external hyperlink can still leave this private site.
- Verify raw captures, ZIPs, `.ready`, dotfiles, PHP and directory listings are
  not served, and mutating HTTP methods are rejected.
- From outside the network, test the hostname and representative image through
  public DNS/tunnel and the external gateway. Neither may return family content.
  Avoid logging page/image bodies in public diagnostics.
- Confirm no Gatus/homepage entry appeared. After a separately approved restart
  or reschedule, recheck content and readiness to prove persistence.

## Rollback and recovery

To stop serving, set replicas `0` through an approved GitOps change. The internal
route will again have no backend Pods. Keep the PVC and private archive. Do not
re-enable the seed Component against the populated volume: import will correctly
refuse it.

For storage loss, use the private Nextcloud ZIP and expected checksum to rebuild
on a **fresh empty PVC**, with serving disabled. Retain the old claim for inspection
if it still exists; a replacement name must be changed consistently in the PVC,
server and seed references. Revalidate and repeat bootstrap/import/activation.
Deleting/replacing a claim needs explicit approval; never clear a volume in place
or remove a backup to force import to succeed.

## Local validation

Render all lifecycle states with the same internal HTTPS route: bootstrap has zero
server replicas and one seed Job; after seed removal both counts are zero;
activation has one server replica and no seed Job. Test NGINX with a read-only
root/site as UID 101, including health, readiness, blocked methods and paths, and
relative directory redirects.

```bash
python3 -m unittest discover -s scripts -p 'test_family_blog_seed.py' -v
mise exec -- kustomize build kubernetes/apps/default/family-blog/app
git diff --check
mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh
```

Local rendering and runtime tests do not substitute for the live privacy checks.
