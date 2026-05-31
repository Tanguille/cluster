---
name: add-app-to-cluster
description: >-
  Deploy new applications to the Kubernetes cluster via FluxCD GitOps. Creates HelmReleases,
  Kustomizations, and namespace configs following repo conventions.

  user: "Deploy jellyfin" → HelmRelease with app-template, HTTPRoute, persistence
  user: "Add uptime-kuma" → Deploy with probes, route, resource limits
  user: "Install prometheus exporter" → HelmRelease with custom scrape config

  Use proactively when the user mentions deploying, installing, adding, or setting up an application.
compatibility: Requires `mise`, `git`, `gh` (for PR), `flux`, `kustomize`, and `shellcheck`; cluster apply needs user approval per AGENTS.md.
---

# Add app to cluster

Deploy applications using FluxCD GitOps patterns in this repository.

## When to use

- New app deployment or major app scaffold in `kubernetes/apps/`.
- User asks to install, deploy, or add a service to the cluster.

## Delegation

| Task | Pattern |
|------|---------|
| kubesearch.dev research | Subagent (app name + namespace) |
| Multiple unrelated apps | Parallel subagents |
| Validation | Sequential after files exist |
| Single-file edits (<5 lines) | Inline |

## Workflow

### 1. Research (subagent)

Use the [k8s-at-home-research](../k8s-at-home-research/SKILL.md) skill (or kubesearch.dev) to find Flux + Helm references and key values to adapt. Spawn a subagent with the app name and namespace preference; return the best exemplar manifest and values to reuse.

### 2. Create worktree

```bash
git worktree add ../<app-name>-worktree -b feat/add-<app-name>
cd ../<app-name>-worktree
```

See [git-worktree-isolation](../git-worktree-isolation/SKILL.md) for isolation patterns.

### 3. Namespace

| Namespace | Purpose |
|-----------|---------|
| `default` | General apps |
| `network` | Proxies, gateways |
| `observability` | Monitoring |
| `media` | Media servers |
| `database` | Databases |
| `security` | Security tools |

Confirm with user before creating a new namespace.

### 4. Structure

```text
kubernetes/apps/<namespace>/<app-name>/
├── ks.yaml
└── app/
    ├── kustomization.yaml
    ├── helmrelease.yaml
    └── ...
```

Scaffold YAML: [references/manifest-templates.md](references/manifest-templates.md).

### 5. HelmRelease essentials

- Chart: `bjw-s/app-template` (pinned version)
- `reloader.stakater.com/auto: "true"` when using ConfigMaps/Secrets
- Probes, resources, persistence as needed
- RWO `ceph-block`: `Recreate` strategy when appropriate

### 6. HTTPRoute

- Hostname: `{{ .Release.Name }}.${SECRET_DOMAIN}`
- Internal routes: parentRef `envoy-internal` in namespace `network`

### 7. Namespace kustomization

Add `./<app-name>/ks.yaml` to `kubernetes/apps/<namespace>/kustomization.yaml`.

### 8. Validate

Replace `<namespace>` and `<app-name>` with your app path:

```bash
mise exec -- yamllint -c .yamllint.yaml kubernetes/apps/<namespace>/<app-name>/
mise exec -- kustomize build kubernetes/apps/<namespace>/<app-name>/
mise exec -- shellcheck scripts/*.sh
```

Or run the full local PR check: `bash .agents/skills/pr-review/scripts/validate-pr.sh`

### 9. PR (ask before push)

```bash
git add .
git commit -m "feat(<namespace>): add <app-name>"
git push -u origin feat/add-<app-name>
gh pr create --title "feat(<namespace>): add <app-name>" --body "Deploy <app-name> to <namespace> namespace"
```

## Anti-patterns

- Skip kubesearch.dev / homelab research when examples exist
- Hardcode domains (use `${SECRET_DOMAIN}`)
- New namespace without user confirmation
- `kubectl apply` bypassing GitOps

## Quick reference

| Task | Command |
|------|---------|
| Validate | `yamllint` + `kustomize build` on app path; `shellcheck scripts/*.sh`; or `validate-pr.sh` |
| Reconcile | `flux reconcile kustomization <name>` (ask user) |
| Logs | `kubectl logs -n <ns> deployment/<app>` |

## Progressive disclosure

- Manifest scaffolds: [references/manifest-templates.md](references/manifest-templates.md)

## Related skills

- [k8s-at-home-research](../k8s-at-home-research/SKILL.md) — homelab manifest examples
- [git-worktree-isolation](../git-worktree-isolation/SKILL.md) — isolated branches

Format reference: [agentskills.io](https://agentskills.io/specification).
