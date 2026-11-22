---
name: pr-review
description: >-
  Review a pull request, Renovate update or local diff in this Flux repository:
  find what changed upstream, what depends on it under kubernetes/, render
  HelmReleases with flate, and state what each check proves. Kritika loads it
  for every review; local reviews also follow references/local.md.
compatibility: flate and gh; locally also crane and the validate-pr.sh tools.
---

# PR review

Flux reconciles `kubernetes/` from `main`: a merge rolls out within minutes.
Secrets are SOPS files: their keys are plaintext and their values are not, so
judge key names and the `secretKeyRef`s that read them.

## A Renovate update

Take from the title, body, labels and diff: the package and its datasource
(container image, Helm chart, GitHub Action, mise or aqua tool, Grafana
dashboard, kernel, Renovate preset), the old and new version or digest, the
update type from the `type/*` label, and the changed files, which are the
places that consume the dependency. An update under `kubernetes/` also takes
[that section](#a-change-under-kubernetes).

The body's `🚦 **Automerge**:` line says whether the PR merges on its own once
its required checks pass; if it does, this review is the last look. A
`groupName` PR moves several packages at once: cover each. The `type/*` label
and a `!` on a major update should agree with the title. Open
`.renovaterc.json5` only when one of these is in doubt.

### Finding the upstream

- An image at `<registry>/<owner>/<repo>` usually comes from `<owner>/<repo>`
  on GitHub. Otherwise the PR body or the image's
  `org.opencontainers.image.source` label names it (see
  [Reading a registry](#reading-a-registry)).
- A chart's source is the HelmRelease's inline `chart.spec`: `url` is the
  registry path, `ref.tag` the version. Under `oci://<registry>/<owner>/...`
  it is that owner's chart; `Chart.yaml`'s `sources` or `home` confirms the
  repository. A `chartRef` to `app-template` reads the shared
  `OCIRepository` in `kubernetes/components/common/`.
- A path under `ghcr.io/home-operations/charts-mirror/<chart>` is a mirror of
  a chart its project does not publish as OCI: `apps/<chart>/metadata.yaml` in
  `home-operations/charts-mirror` names the upstream Helm repository; the
  changelog lives in that project's GitHub repository.
- Chart version and app version differ. Read the chart's changelog, and the
  application's too when `appVersion` moved.
- A digest-only update moved no version. Compare the old and new digests'
  `created` time and `org.opencontainers.image.revision` and say why the tag
  was rebuilt. On `ghcr.io/home-operations/*` images the revision is a
  `home-operations/containers` commit, not the app's: its diff says what
  changed in the build.
- `revenz/fileflows` has no source label and no public repository. Its
  notes are on `https://fileflows.com/docs/versions` per month, not per
  build: a rebuild inside the month stays unverified, so say so.
- A note that says "see #2785", on an entry that matters here, is read with
  `gh issue view` or `gh pr view`; take an issue to the merged PR that
  closed it (`gh pr list -R <repo> --search "#<n>" --state merged`).

### What breaks

Flag in each release: `BREAKING CHANGE`, `⚠` or `!:` markers; removed or
renamed Helm values, CRD fields, environment variables and flags; a new
minimum Kubernetes, Flux, Talos or kernel version; one-way schema or data
migrations; changed defaults (authentication, storage class, ports, probes);
deprecations that became errors; new required keys with no default; and
label values that changed while the key stayed, or resource names that
gained or lost a prefix (settle which from the render): both break selectors
on the old value while a search for the key still matches. Minor and patch
updates carry these too.

An update also moves images that are nowhere in the diff: a chart or
operator that changes its default images (rook-ceph, CloudNativePG and other
operators). Nothing here pins them, so Renovate never raises them. Name each
with its old and new version: from the `image:` lines of the `flate diff`
when the chart renders them, otherwise from one
`gh api repos/<owner>/<repo>/compare/<old>...<new> --jq '.files[] | select(.filename | test("values.yaml|defaults")) | .patch'`.

Search every removed or renamed name at once
(`rg -e <a> -e <b> kubernetes/`). Give each entry that applies its own
summary line with what it changes here, and the rest one line: "not
applicable here: A, B, C", with the search. An entry is a finding only when
something here depends on it.

### What it means for us

The `renovate` rule asks for the notable release-note entries at the end of
the summary. Search `kubernetes/` once for comments that cite the upstream
repository's issues: a release that fixes one means the workaround can go,
so say which line. These are notes, not findings.

## A change under `kubernetes/`

Conventions are in `kubernetes/AGENTS.md`. Also check that a route on
`envoy-external` is meant to be public.

### Render it

When a chart version or HelmRelease values change, render from the
repository root. `-p kubernetes` is required: a namespace directory alone
finds no HelmRelease, because the `ks.yaml` paths start at the root.

```sh
flate diff hr <name> -n <namespace> -p kubernetes --base origin/main --no-progress -o diff
```

- Run `flate build hr` with the same `-n` and `-p` (and
  `--only-kinds <Kind>`) only to quote a field the diff does not show.
- A change under `kubernetes/components/` moves every app that includes it
  (the `app-template` `OCIRepository` among them): render up to three that
  use different features, such as persistence, routes and several
  controllers.
- `--base origin/main` (flate fetches it, even in Kritika's checkout)
  compares against the latest `main`. When a render line is not in the PR's
  diff, rerun with `--base <merge-base-sha>` from
  `gh api repos/<owner>/<repo>/compare/<base-sha>...<head-sha> --jq .merge_base_commit.sha`.
- A render that finds no such HelmRelease exits 1 with `no HelmRelease named
  "<name>" in --path or --path-orig`. Check the name and namespace before
  you read it as "no change".
- A render that succeeds proves only that the tag exists and the values fit
  the schema, nothing about runtime. A failure the change caused is a
  finding: a value the chart schema rejects, a template that errors on these
  values. A source flate could not fetch says nothing about the change; say
  which render you could not inspect.
- The render includes the cluster-apps patches from
  `kubernetes/flux/cluster/ks.yaml`. It leaves out Secrets and CRDs, and
  templates that branch on the Kubernetes version see flate's bundled one.

### What depends on it

Ask whether anything here depends on the old thing, not whether this
repository sets it. Search `kubernetes/` for:

- **a label key or value**: `matchLabels` and `selector` fields of
  monitors, network policies (including the `*.home.arpa/*` grant labels)
  and disruption budgets, and label matchers in PromQL rules and dashboards;
- **a resource name**: route backends, cross-namespace DNS
  (`<name>.<namespace>.svc.cluster.local`), selectors, and Flux `dependsOn`
  and `healthChecks`;
- **a value, flag or CRD field**: HelmRelease `values`, `valuesFrom`,
  `postRenderers`, kustomize patches including the cluster-apps ones, and
  the custom resources other apps create from the dependency's CRDs
  (`apiVersion: <group>`).

An app configured through its own UI keeps that configuration on its volume,
not in git: name an affected setting there as one the review could not
verify.

Confirm a rename or new value from the rendered field before prescribing it.
Only when the render cannot show it (a value not set here), read the chart
template at the new tag:
`gh api -H "Accept: application/vnd.github.raw+json" repos/<owner>/<repo>/contents/<path>?ref=<tag>`.

A finding anchors to the changed line; name the dependent file and line in
its explanation.

## What a check proves

- A prerequisite that comes with a release (kernel, Kubernetes) is checked
  on its own, even when its changed defaults are already set here, against
  the targets in `kubernetes/apps/system-upgrade/tuppr/upgrades/`. A target
  in git does not prove every node runs it: say the line rests on the
  target.
- Evidence goes in the claim: "`auth.enabled: true` already set in the
  HelmRelease values", not "auth checked".

## Reading a registry

In Kritika, use `fetch_oci` with the registry path and tag or digest (no
`oci://` prefix for a chart); locally, `crane` (references/local.md). Tags
named `sha256-…` are signatures, not versions. Every node is `linux/amd64`:
a new tag without it is a finding, and for a multi-platform index read the
`linux/amd64` config, not the first one.
