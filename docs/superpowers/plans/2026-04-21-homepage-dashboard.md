# Homepage Dashboard Implementation Plan

**Spec:** `docs/superpowers/specs/2026-04-21-homepage-design.md`
**Branch:** `feat/homepage-dashboard` (no worktree).
**Goal:** Ship gethomepage.dev at `home.${SECRET_DOMAIN}` with the inventory the spec defines: bjw-s app-template, whole-dir ConfigMap mount, Hybrid discovery (annotations + central ConfigMap), 19 Tier 1 widgets + 23 Tier 2 tiles + 3 static TLSRoute entries (TrueNAS/OPNsense/IPMI), 6 topical tabs, global header with kromgo customapi stats.

---

## Process Instructions

- After each step, update this plan with current status.
- Pause for user confirmation before proceeding to next task.
- Suggest the continuation prompt at each pause.
- After the final task, fold this plan into the spec's "Implementation notes" section or delete if fully self-evident from the shipped files.
- **Every prompt verifies the branch before any work:** `git branch --show-current` must print `feat/homepage-dashboard`.

---

## Task 1: Scaffold homepage app

Create the full app skeleton with real config but **placeholder secrets**. Dashboard will boot with just the global header + empty tabs (no tiles yet — annotations come in later tasks).

**Files to create:**

- `kubernetes/apps/default/homepage/ks.yaml`
- `kubernetes/apps/default/homepage/app/kustomization.yaml`
- `kubernetes/apps/default/homepage/app/helmrelease.yaml`
- `kubernetes/apps/default/homepage/app/secret.sops.yaml` (SOPS-encrypted, placeholder values)
- `kubernetes/apps/default/homepage/app/resources/settings.yaml`
- `kubernetes/apps/default/homepage/app/resources/bookmarks.yaml`
- `kubernetes/apps/default/homepage/app/resources/widgets.yaml`
- `kubernetes/apps/default/homepage/app/resources/kubernetes.yaml`
- `kubernetes/apps/default/homepage/app/resources/services.yaml` (TrueNAS/OPNsense/IPMI entries)

**Files to modify:**

- `kubernetes/apps/default/kustomization.yaml` (add `./homepage/ks.yaml`)

**Steps:**

- [ ] **1.1** Verify branch: `git branch --show-current` → `feat/homepage-dashboard`.
- [ ] **1.2** Write all files listed above.
- [ ] **1.3** `mise exec -- kubeconform -strict kubernetes/apps/default/homepage/` — must pass.
- [ ] **1.4** `mise exec -- kubeconform -strict kubernetes/` — full-repo pass.
- [ ] **1.5** Encrypt `secret.sops.yaml` with sops (placeholder values for now).
- [ ] **1.6** Commit: `feat(homepage): scaffold dashboard app with tabs, widgets, and kromgo-fed header`.

---

## Task 2: Deploy and verify blank dashboard

No file changes — verification only.

- [ ] **2.1** Ask user: `task reconcile` (or they run it).
- [ ] **2.2** Verify: `kubectl -n default get pod,httproute -l app.kubernetes.io/name=homepage`.
- [ ] **2.3** Verify `home.${SECRET_DOMAIN}` resolves and loads. Should render: header with greeting/datetime/search/k8s/resources/kromgo-customapi; six tabs; Home tab shows bookmark groups; other tabs empty.
- [ ] **2.4** Check logs: `kubectl -n default logs deploy/homepage | head -30` — confirm no fatal errors, confirm k8s discovery is active.

**Stop condition:** if k8s widgets fail (RBAC), config fails to load (crash), or `HOMEPAGE_ALLOWED_HOSTS` blocks requests — debug before advancing.

---

## Task 3: Tier 1 annotations — Media namespace (10 apps)

Add `gethomepage.dev/*` annotations to each HTTPRoute's `route.<name>.annotations` in each app's helmrelease.yaml. Use in-cluster URLs (`http://<svc>.media.svc.cluster.local[:<port>]`) for `widget.url`; keep `widget.key` referencing `{{HOMEPAGE_VAR_*}}`.

**Files to modify:**

- `kubernetes/apps/media/sonarr/app/helmrelease.yaml` → Management group, widget.type: sonarr
- `kubernetes/apps/media/radarr/app/helmrelease.yaml` → Management, widget.type: radarr
- `kubernetes/apps/media/bazarr/app/helmrelease.yaml` → Management, widget.type: bazarr
- `kubernetes/apps/media/prowlarr/app/helmrelease.yaml` → Management, widget.type: prowlarr
- `kubernetes/apps/media/qbittorrent/app/helmrelease.yaml` → Downloaders, widget.type: qbittorrent
- `kubernetes/apps/media/jellyfin/app/helmrelease.yaml` → Library, widget.type: jellyfin
- `kubernetes/apps/media/jellystat/app/helmrelease.yaml` → Library, widget.type: jellystat
- `kubernetes/apps/media/seerr/app/helmrelease.yaml` → Requests, widget.type: jellyseerr
- `kubernetes/apps/media/unpackerr/app/helmrelease.yaml` → Processing, widget.type: unpackerr
- `kubernetes/apps/media/fileflows/app/helmrelease.yaml` → Processing, widget.type: fileflows

**Steps:**

- [ ] **3.1** For each file: survey the current `service.<name>.ports.http.port` → that determines the in-cluster URL port.
- [ ] **3.2** Add annotation block to each HTTPRoute's annotations.
- [ ] **3.3** `mise exec -- kubeconform -strict kubernetes/apps/media/` — pass.
- [ ] **3.4** Commit: `feat(homepage): add media tier-1 widget annotations`.

---

## Task 4: Tier 1 annotations — AI + Apps + Observability + Security (9 apps)

Same annotation pattern as Task 3, different files.

**Files to modify:**

- `kubernetes/apps/ai/ollama/app/helmrelease.yaml` → Models, widget.type: ollama
- `kubernetes/apps/default/immich/app/helmrelease.yaml` → Portals, widget.type: immich (currently commented out in parent kustomization — skip if still disabled; flag to user)
- `kubernetes/apps/default/nextcloud/app/helmrelease.yaml` → Portals, widget.type: nextcloud
- `kubernetes/apps/default/searxng/app/helmrelease.yaml` → Utilities, widget.type: searxng
- `kubernetes/apps/default/changedetection/app/helmrelease.yaml` → Utilities, widget.type: changedetectionio
- `kubernetes/apps/observability/grafana/app/helmrelease.yaml` → Metrics, widget.type: grafana
- `kubernetes/apps/observability/kube-prometheus-stack/app/helmrelease.yaml` → Metrics, widget.type: prometheus (URL: `http://prometheus-operated.observability.svc.cluster.local:9090`)
- `kubernetes/apps/observability/gatus/app/helmrelease.yaml` → Status, widget.type: gatus
- `kubernetes/apps/security/crowdsec/app/helmrelease.yaml` → Security, widget.type: crowdsec

**Steps:**

- [ ] **4.1** Same port-survey + annotation edits as Task 3.
- [ ] **4.2** kubeconform pass.
- [ ] **4.3** Commit: `feat(homepage): add tier-1 widget annotations for ai, default, observability, security`.

---

## Task 5: Tier 2 tile-only annotations (~22 apps)

Add `gethomepage.dev/*` annotations **minus** `widget.*` keys (tile + href only). Group per the spec's Tier 2 listing.

**Files to modify (grouped):**

- **AI** (5): open-webui, llama-server, opencode, moltis, toolhive
- **Apps/default** (5): karakeep, picoshare, it-tools, dumbassets, spoolman
- **Media** (7): flaresolverr, recyclarr (no UI — skip if no HTTPRoute), cross-seed (no UI — skip if no HTTPRoute), deduparr, streamystats, brrpolice, wizarr
- **Observability** (2): victoria-logs, kromgo
- **Web3** (1): `web3/monero/dashboard` (hostname `p2pool.${SECRET_DOMAIN}`, display "Monero")

**Steps:**

- [ ] **5.1** For each app above, verify an HTTPRoute exists in its helmrelease. If none, drop from inventory and note here (`recyclarr`, `cross-seed`, and possibly others are CLI-only with no UI — confirm).
- [ ] **5.2** Add annotation block (no widget.* keys).
- [ ] **5.3** kubeconform pass.
- [ ] **5.4** Commit per namespace group: `feat(homepage): add tier-2 tile annotations for <namespace>`.

---

## Task 6: Populate real API keys in SOPS secret

**User task** — requires access to each app's UI to generate API keys.

- [ ] **6.1** User decrypts `kubernetes/apps/default/homepage/app/secret.sops.yaml` with `sops`.
- [ ] **6.2** User fills real values for all `HOMEPAGE_VAR_*` keys per the spec's Secrets section.
- [ ] **6.3** User re-encrypts and commits: `feat(homepage): populate widget credentials`.
- [ ] **6.4** `task reconcile` (user).

Agent role: provide the full list of keys and the commands; hand over for user to run.

---

## Task 7: Final verification + documentation pass

- [ ] **7.1** Open `home.${SECRET_DOMAIN}`. Verify: all 19 Tier 1 widgets render live data (not "API error"); all 22 Tier 2 tiles are clickable and land on each app; 3 static External tiles (TrueNAS/OPNsense/IPMI) click through to TLSRoute hostnames; global header shows cluster stats.
- [ ] **7.2** Tab switch sanity: each of six tabs shows the right groups; URL fragments `#media`, `#ai`, etc. deep-link correctly.
- [ ] **7.3** Logs clean: `kubectl -n default logs deploy/homepage --tail=50` shows only successful polls.
- [ ] **7.4** If any widget returns errors: either fix the credential, fix the URL, or move that app to Tier 2 (drop widget block, keep tile) and commit the downgrade.
- [ ] **7.5** Fold this plan into the spec (append an "Implementation notes" section with learnings) and delete the plan file. Commit: `docs(homepage): consolidate implementation notes into spec`.
- [ ] **7.6** Merge to main via PR (user triggers): `gh pr create`.

---

## Rollback

- Remove `./homepage/ks.yaml` from `kubernetes/apps/default/kustomization.yaml`; Flux prunes the HelmRelease and all dependents.
- Annotation reverts are safe per-app — annotations are additive metadata, no behavioral impact on the underlying apps.

---

## Status

- [x] Task 1: Scaffold homepage app — **DONE** (commit `82de1256b`)
- [ ] Task 2: Deploy and verify blank dashboard — **DEFERRED** (user opted to stack annotation work on the same branch; verification happens once after all commits)
- [x] Task 3: Tier 1 media annotations — **DONE** (commit `50f79fa26`; unpackerr uses Service annotations, no HTTPRoute added)
- [x] Task 4: Tier 1 ai/default/observability/security — **DONE** (commit `0b55afb87`; immich skipped — commented out; crowdsec added to services.yaml; grafana annotations on raw HTTPRoute)
- [ ] Task 5: Tier 2 tile annotations — **IN PROGRESS**
- [ ] Task 6: Populate real API keys in SOPS secret
- [ ] Task 7: Final verification + doc pass
- [ ] Task 3: Tier 1 annotations — Media
- [ ] Task 4: Tier 1 annotations — AI + default + observability + security
- [ ] Task 5: Tier 2 tile annotations
- [ ] Task 6: Populate real API keys in SOPS secret
- [ ] Task 7: Final verification + doc pass
