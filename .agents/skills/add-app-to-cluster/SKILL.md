---
name: add-app-to-cluster
description: >-
  Deploy new applications to the Kubernetes cluster via FluxCD GitOps. Creates HelmReleases,
  Kustomizations, and namespace configs following repo conventions.

  user: "Deploy jellyfin" → HelmRelease with app-template, HTTPRoute, persistence
  user: "Add uptime-kuma" → Deploy with probes, route, resource limits
  user: "Install prometheus exporter" → HelmRelease with custom scrape config

  Use proactively when the user mentions deploying, installing, adding, or setting up an application.
compatibility: Requires `mise`, `git`, `flate`, and `shellcheck` (falls back to `kustomize`/`flux` if `flate` is unavailable); cluster apply needs user approval per AGENTS.md.
---

# Add app to cluster

Deploy applications using FluxCD GitOps patterns in this repository.

## When to use

- New app deployment or major app scaffold in `kubernetes/apps/`.
- User asks to install, deploy, or add a service to the cluster.

## Workflow

### 1. Research (subagent)

Use [k8s-at-home-research](../k8s-at-home-research/SKILL.md) to find exemplar manifests — prefers kubesearch MCP when available. Spawn a subagent with the app name and namespace; return the best values and adaptation notes.

Confirm before scaffolding: image + tag (plain upstream tag — Renovate pins digests, never invent one), port, route internal/external, persistence (→ kopiur component), secrets (→ `secret.sops.yaml`), config files (→ configMapGenerator), Flux `dependsOn`.

### 2. Create worktree

Use the [git-worktree-isolation](../git-worktree-isolation/SKILL.md) recipe: `.worktrees/feat-add-<app-name>` branched from `origin/main`, with local config copied in.

### 3. Namespace

List existing namespaces with `ls kubernetes/apps/` and pick the best fit; confirm with the user before creating a new one.

### 4. Structure

```text
kubernetes/apps/<namespace>/<app-name>/
├── ks.yaml
└── app/
    ├── kustomization.yaml
    ├── helmrelease.yaml
    └── ...
```

Scaffold YAML: [references/manifest-templates.md](references/manifest-templates.md). When in doubt, mirror a recent real app — templates may lag:

| Exemplar | Demonstrates |
|----------|--------------|
| `kubernetes/apps/media/qui` | kopiur persistence, dependsOn, probes, hardened securityContext |
| `kubernetes/apps/media/sonarr` | sops secret, valuesFrom, homepage annotations |
| `kubernetes/apps/media/recyclarr` | configMapGenerator config files |

### 5. HelmRelease essentials

- Chart: `bjw-s/app-template` (pinned version)
- `reloader.stakater.com/auto: "true"` when using ConfigMaps/Secrets
- Probes, resources, persistence as needed
- RWO `ceph-block`: `Recreate` strategy when appropriate

### 6. HTTPRoute

- Hostname: `{{ .Release.Name }}.${SECRET_DOMAIN}`
- Internal routes: parentRef `envoy-internal` in namespace `network`
- External routes: parentRef `envoy-external` in namespace `network` (same shape, only the parentRef name changes)

### 7. Namespace kustomization

Add `./<app-name>/ks.yaml` to `kubernetes/apps/<namespace>/kustomization.yaml`. Keep alphabetical order if the file uses it; otherwise append near related apps.

### 8. Validate

```bash
mise exec -- flate test all
```

`${APP}`/`${SECRET_DOMAIN}` staying literal in the output is expected — Flux postBuild substitutes them. `flate` renders the HelmRelease (catches Helm template errors `kustomize build` can't see); if it's unavailable, fall back to `mise exec -- kustomize build kubernetes/apps/<namespace>/<app-name>/app/` (Kustomization-only, no Helm render).

Or run the full local PR check: `bash .agents/skills/pr-review/scripts/validate-pr.sh`

### 9. PR (ask before push)

Show the user the created files and get confirmation before committing. Stage the new app
directory and the namespace `kustomization.yaml` by path, commit as
`feat(<namespace>): add <app-name>`, then push and open the PR per the
[atomic finish rule](../handoff/SKILL.md#the-atomic-finish-rule).

## Anti-patterns

- Non-app-template chart without its own `ocirepository.yaml` — the shared `app-template` OCIRepository covers only that chart
