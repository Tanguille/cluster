---
name: live-pr-test
description: Build an unreleased PR/fork branch of any cluster dependency, deploy it to the live cluster behind suspended Flux, capture e2e evidence, and revert to a provably clean GitOps state. Use when asked to "test this PR live", "verify on the cluster", or validate an upstream change before it ships.
---

# Live PR Test

Deploy an unreleased build to production long enough to capture e2e proof, then
return the cluster to exactly the state Flux owns. The revert runbook is written
before the first mutating command. Everything here is runtime-only: no commits
to the cluster repo, no pushes.

## Input
- `$ARGUMENTS` — the PR/branch/repo to test, and optionally the target workload.

## Step 1 — Risk verdict (before touching anything)

Set up the ground truth first:
- Clone into the scratchpad and check out the PR: `gh repo clone <org>/<repo>`
  then `gh pr checkout <n>`. All Step 1 diffs and the Step 2 build run from
  this checkout (the upstream remote is `origin`).
- Map the dependency to its workload: grep `kubernetes/` for the repo, image,
  or chart name — the matching `kubernetes/apps/<namespace>/<app>/` gives the
  HelmRelease/Kustomization and namespace. No match → stop and ask.
- The "released tag" for diffing is the **app version of the running image**
  (`kubectl get deploy <d> -n <ns> -o jsonpath='{.spec.template.spec.containers[*].image}'`
  matched to an upstream git tag) — never the chart version `flux get hr` shows.

Then classify the change:

**One-way doors — STOP, report the risk, and get explicit user go-ahead.
Prefer a throwaway CR/namespace over touching the live workload:**
- CRD schema changes. Helm skips `crds/` on upgrade, and reverting the chart
  does NOT restore old CRDs (this cluster lost snapshot CRDs for 44h this way).
  Diff: `git log <released-tag>..HEAD --stat -- api/ config/crd/ '**/crds/'`
  (chart-shaped repos keep CRDs in `crds/` inside the chart).
- Database schema or on-disk format migrations (CNPG version bumps, kopia
  index, ceph). The old version may not read what the new one wrote.
- Any workload — Deployment or StatefulSet — whose test build writes to a PVC
  or runs app-level migrations at startup. Most app-template apps here are
  PVC-backed Deployments; "it's not a StatefulSet" is not a safe-harbor.
- Webhooks or finalizers the test build registers — they outlive the revert.

**Default NO — critical path; test in kind/a throwaway node instead:**
cilium, coredns, rook-ceph, flux itself, anything node-level
(Talos, kernel, system extensions — those need their own plan).

**Green — proceed:** stateless controllers/operators, exporters, dashboards,
anything whose only persistent output is metrics/logs.

For operators, also check upstream drift: is `origin/main` ahead of the
running release beyond the PR (`git log <released-tag>..origin/main --stat`)?
Extra commits touching `api/` or `config/` mean the test image is not
"release + this PR" and the verdict must say so.

Post the verdict as one short block: shape, artifact, SAFE TO REVERT or RISKY
(with reasons), the runbook path (Step 3), and the revert command list.
If RISKY, wait for confirmation.

## Step 2 — Build and host the artifact

- Tag unambiguously: `ghcr.io/tanguille/<name>:pr-<n>-<shortsha>`. Never
  `latest`.
- podman trap: `.dockerignore` files using `**` + `!**/*.go` negation silently
  exclude all source under podman. Don't patch upstream's file — build with an
  external ignorefile (`--ignorefile` containing just `.git`, `bin`, `dist`).
- Check `podman login --get-login ghcr.io` before touching credentials. Never
  pipe tokens through shell yourself — if login is needed, hand the command to
  the user.
- GHCR packages default private and there is no API to flip visibility.
  Private is fine, but a pull secret means planting a token in the cluster:
  warn about its scope and get an explicit OK, or ask the user to flip the
  package public in the UI. Note upfront that deleting the package later needs
  `delete:packages` or a UI click.
- Chart-artifact PRs: skip GHCR — `helm template` from the PR checkout with
  the HR's live values, `kubectl apply` the diffed manifests under the same
  suspend, and add an explicit `kubectl delete` to the runbook for every NEW
  resource the chart introduces (a forced upgrade resets modified fields but
  never removes additions).

## Step 3 — Write the revert runbook, then deploy

Write the runbook BEFORE the first patch, to a path that survives this
session: `~/.local/state/live-pr-test/<ns>-<name>.md` — NOT the session
scratchpad, which dies with the session. Include the path in the risk-verdict
message so it lives in the transcript, and pin it to the cluster too:
`kubectl annotate <kind> <name> -n <ns> live-pr-test/runbook=<path>` on the
object you suspend, so anyone finding it suspended finds the trail.

The runbook records the ORIGINAL image (read it before patching) and every
field you add, then:

```
# chart-managed workload (HelmRelease route)
flux resume helmrelease <hr> -n <ns>
flux reconcile helmrelease <hr> -n <ns> --force --with-source
# --force is load-bearing: without driftDetection (no HR in this repo sets it),
# a plain reconcile skips the upgrade when chart+values are unchanged and the
# patched image would stay live forever. Fallback if flux is unavailable:
kubectl set image deploy/<d> -n <ns> <container>=<original-image>
kubectl patch deploy <d> -n <ns> --type json -p '[{"op":"remove","path":"/spec/template/spec/imagePullSecrets"}]'
kubectl delete secret <pull-secret> -n <ns>
kubectl annotate helmrelease <hr> -n <ns> live-pr-test/runbook-

# CR- or manifest-managed workload (Kustomization route)
flux resume kustomization <ks> -n flux-system
flux reconcile kustomization <ks> -n flux-system --with-source
# kustomize-controller's SSA force-apply DOES revert field drift on reconcile —
# no --force flag needed; the asymmetry with helm-controller is deliberate.
```

Then deploy, by shape:
- **Chart-managed Deployment/DaemonSet:** `flux suspend helmrelease <hr> -n <ns>`,
  then patch the live workload image with kubectl (for a DaemonSet, wait for
  `kubectl rollout status ds/<name>` before capturing evidence). Record EVERY
  field you add that the chart doesn't manage (imagePullSecrets, env, args):
  Helm's 3-way merge only touches chart-owned fields, so each addition needs
  its own explicit JSON `remove` patch in the runbook.
- **Operator-managed CR workloads** (llmkube InferenceService, CNPG Cluster,
  Dragonfly, LiteLLMProxy, MCPServer): never patch the child Deployment/pods —
  the running operator reverts it within seconds, and suspending the operator's
  own HelmRelease does not stop it. Suspend the Kustomization that owns the CR
  (read the `kustomize.toolkit.fluxcd.io/name`/`namespace` labels on the CR)
  and patch the CR's image field (InferenceService `spec.image`, Cluster
  `spec.imageName`). Don't scale operators to 0; if truly unavoidable, the
  original replica count goes in the runbook as an explicit restore line.
- **Plain manifests:** suspend the owning Kustomization (same labels, on the
  workload) and patch the object directly.

Pull secret, if needed — create it from podman's auth file so no token
touches the shell:
`kubectl create secret generic <pull-secret> -n <ns> --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=${XDG_RUNTIME_DIR}/containers/auth.json`

Keep the suspend window short. If the session may end mid-test, the runbook
file plus the annotation are the handoff.

## Step 4 — Verify e2e: evidence, not vibes

- Capture BEFORE state first, into scratchpad files: metrics scrape,
  `kubectl get -o yaml` of the target, logs, plus a cluster inventory the
  revert will be diffed against:
  `kubectl get crd,validatingwebhookconfigurations,mutatingwebhookconfigurations -o name | sort`
  and the list of already-suspended Flux objects.
- Test the PR's specific claims against real workloads; capture AFTER state.
- Prove no collateral: neighbor pod uptimes unchanged, `flux get hr -A` clean
  elsewhere.
- Known artifact: `kubectl run --rm -i` duplicates every output line
  (attach + log). Count unique lines before claiming a duplication bug.
- Verify shell claims honestly: piping through `head` swallows exit codes —
  check `$status` before printing "done".
- Record the evidence where it counts (PR body, issue comment) — a test
  nobody can see didn't happen.

## Step 5 — Revert and prove it

- Run the runbook: remove added fields, resume, forced reconcile.
- Prove clean, per route: HelmRelease → live workload matches
  `helm get manifest` (image, no leftover fields) and Helm history shows the
  expected revision; Kustomization → the CR matches its manifest in git.
- Diff the Step 4 inventory: CRDs/webhooks list identical (operator builds
  often self-install these at boot regardless of the PR diff), and no Flux
  object suspended that wasn't suspended before —
  `kubectl get kustomizations,helmreleases -A -o json | jq -r '.items[] | select(.spec.suspend==true) | .metadata.name'`
  (this cluster once carried an unnoticed suspended ks for weeks).
- Remove the `live-pr-test/runbook` annotation. Delete the pull secret.
  Delete the GHCR package or tell the user it needs a UI click — report the
  actual result, not the intended one.
- Delete the runbook and scratchpad temp files except the evidence you cited.
- Final report: risk verdict recap, before/after evidence, revert proof, and
  anything left for the user.
