# Private family blog

Read-only historical website at `https://family-blog.${SECRET_DOMAIN}` for the
home network and VPN, **without a login**. Comments and guestbook are preserved
pages, not submission forms. Missing-image placeholders remain missing images.
Hosting this copy does not remove copies on the original site or Internet Archive.
Historical form controls may remain visible, but CSP prevents submission. A legacy
external archive-toolbar badge is also blocked; do not weaken CSP to load it.

## Safety and layout

- NGINX serves only `/srv/site`, the verified archive's `offline/` tree. The PVC
  root is mounted read-only in NGINX at `/srv`; ZIP, raw captures and metadata are
  not served. The temporary month-link repair init container alone has write access.
- The dedicated `family-blog` PVC is 1Gi, RWO, `ceph-block`. It is protected from
  Flux pruning, **not** from manual deletion or storage failure. The storage
  class's reclaim policy is Delete.
- This derived copy intentionally does not use automatic Kopiur restore: the
  unchanged private ZIP and its checksum in Nextcloud are the recovery source.
  Keep those and original photos intact. Do not mount Nextcloud homes here.
- No family content, archive names/checksums, credentials or private recovery URLs
  belong in Git, public logs, images or registries.
- Normal operation is one server replica with no seed resources. The imported
  content stays on the retained PVC; recovery tooling is available in Git history.
  Seed removal, volume release and privacy checks must precede activation.

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

## 2. Finish cleanup, then activate

Keep cleanup and activation in separate, ordered GitOps changes. A successful
import does not authorize serving before the privacy checks pass.

1. Approve/push the seed cleanup, keeping server replicas `0`. After reconciliation,
   confirm the seed Job, Pods and ConfigMap are gone and the volume is released.
   Never delete the dedicated PVC as part of cleanup.

   Read-only checks (Job/Pod/ConfigMap commands must print nothing; attachment list
   must be `[]`). Recheck if pruning or CSI detachment is still in progress; do not
   delete a VolumeAttachment to force this checkpoint:

   ```bash
   mise exec -- kubectl -n default get job family-blog-seed --ignore-not-found -o name
   mise exec -- kubectl -n default get pods -l job-name=family-blog-seed -o name
   mise exec -- kubectl -n default get configmap family-blog-seed --ignore-not-found -o name
   PV=$(mise exec -- kubectl -n default get pvc family-blog -o jsonpath='{.spec.volumeName}')
   test -n "$PV"
   mise exec -- kubectl get volumeattachments.storage.k8s.io -o json |
     mise exec -- jq --arg pv "$PV" \
       '[.items[] | select(.spec.source.persistentVolumeName == $pv) |
         {name: .metadata.name, attached: .status.attached}]'
   ```

2. Only after volume release and all privacy checks, remove the family-blog
   controller's `replicas: 0` override in `app/helmrelease.yaml` through a separately
   approved change. The chart defaults to one replica; verify that rendered result
   rather than retaining a redundant `replicas: 1` value.
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

## Monthly navigation repair

The original offline archive contains month links that return to the homepage,
even though their corresponding monthly pages exist. The temporary startup helper
changes only those links' `href` values in the served copy to existing local pages;
it does not change the original ZIP, backup, images, appearance or security policy.
Unrelated external hyperlinks remain unchanged.

The deployment must render with `Recreate`: the old server stops before the repair
init container writes to the retained PVC, and NGINX starts only after success.
The helper validates targets before writing, atomically replaces changed HTML and
updates the parent `.ready` file's derived file/byte inventory last. It can safely
finish an interrupted attempt; never rerun the seed importer on this volume.
Its default is dry-run; only `--apply` writes. Logs contain aggregate counts only.

After startup succeeds, verify month links from the homepage, a post and a monthly
page, all monthly destinations, and unchanged images. Then remove the helper,
synthetic tests, generated ConfigMap and temporary init/mount overrides through a
second approved GitOps change. NGINX must retain its read-only mount throughout.

## Rollback and recovery

To stop serving, set replicas `0` through an approved GitOps change. The internal
route will again have no backend Pods. Keep the PVC and private archive. Do not
re-enable the seed Component against the populated volume: import will correctly
refuse it.

For storage loss, use the private Nextcloud ZIP and expected checksum to rebuild
on a **fresh empty PVC**, with serving disabled. Retain the old claim for inspection
if it still exists; a replacement name must be changed consistently in the PVC,
server and restored seed references.
Deleting/replacing a claim needs explicit approval; never clear a volume in place
or remove a backup to force import to succeed.

The merged bootstrap commit preserves the importer, tests and original runbook:
`cc2cd762ea223d86fbf08175da0cf81aadebc64e` (PR #6671). On a recovery branch, restore
only the tooling, not the old application configuration:

```bash
git restore --source=cc2cd762ea223d86fbf08175da0cf81aadebc64e -- \
  kubernetes/apps/default/family-blog/seed scripts/test_family_blog_seed.py
```

Then:

1. Review restored image versions against upstream, run the restored importer
   tests and add `../seed` as a Component in `app/kustomization.yaml`. Point the
   seed and server at the approved fresh claim; keep server replicas `0`.
2. Deploy the reviewed recovery change after approval. Require a single Ready
   seed Pod, an empty destination (apart from filesystem `lost+found`) and an
   existing `/tmp/archive.fifo`. Do not overlap seed and serving Pods.
3. Obtain the original ZIP and independently recorded expected SHA256 from the
   private Nextcloud recovery record. Verify locally without changing the source.
   Do not depend on temporary files left on the original capture machine.
4. **Do not reuse the historical direct-to-FIFO upload command.** A prior exec
   transfer truncated stdin; checksum rejection prevented extraction. Buffer the
   complete checksum-line-plus-ZIP frame in bounded memory in the seed container,
   verify its exact length and frame SHA256, then open the FIFO once and write
   locally. A rejected transfer must not open the FIFO. Use a reviewed operator
   procedure with a 64MiB frame cap and measured headroom under the 256MiB
   container limit. `/tmp` is only 16MiB, and `lost+found` is not an upload staging
   directory.
5. Require Job completion and logged file/byte counts matching the private recovery
   record. The importer verifies the ZIP and every extracted file, publishes `site`
   atomically, and writes `.ready` last. Only `offline/` content is extracted.
6. Remove the restored seed Component, manifests and tests through GitOps. Confirm
    resource removal and volume release, then repeat privacy checks and activation.
7. The preserved ZIP still has the original broken month links. Before considering
   recovery complete, repeat the reviewed monthly navigation repair above and its
   separate cleanup. The derived `.ready` byte count will differ from the pristine
   import; the private source checksum remains unchanged.

On any failure, stop and inspect privately. Do not automatically repeat the
upload, recreate the Job or remove abandoned staging. A completed Job cannot
accept another upload; a populated volume must never be reseeded.

## Local validation

Render the cleanup state with zero server replicas and no seed Job/ConfigMap;
activation must have one server replica, no seed resources and the same internal
HTTPS route. Test NGINX with a read-only
root/site as UID 101, including health, readiness, blocked methods and paths, and
relative directory redirects.

```bash
mise exec -- kustomize build kubernetes/apps/default/family-blog/app
git diff --check
mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh
```

Local rendering and runtime tests do not substitute for the live privacy checks.
When restoring the importer for recovery, also run
`python3 -m unittest discover -s scripts -p 'test_family_blog_seed.py' -v` before
deploying it.
