---
name: add-app-to-cluster
description: |-
  Deploy new applications to the Kubernetes cluster via FluxCD GitOps. Creates HelmReleases, Kustomizations, and namespace configs following repo conventions.

  user: "Deploy jellyfin" → Create HelmRelease with app-template, HTTPRoute, and persistence
  user: "Add uptime-kuma" → Deploy with probes, route, and resource limits
  user: "Install prometheus exporter" → Create HelmRelease with custom scrape config

  Capabilities: Namespace selection, HelmRelease templating, HTTPRoute setup, validation (kubeconform), Flux reconciliation.

  Use proactively when: User mentions deploying, installing, adding, or setting up any application/service.
---

# Add App to Cluster

Deploy applications to the Kubernetes cluster using FluxCD GitOps patterns.

## When to Delegate to Subagents

**PARALLEL (independent tasks):**

- Research on kubesearch.dev - Spawn subagent with minimal context (app name, namespace preference)
- Multiple unrelated app deployments - Each can be a separate subagent

**SEQUENTIAL (dependencies):**

- Validation → Must complete after file creation
- PR creation → Must complete after validation passes

**INLINE (simple, fast tasks):**

- Single file edits (< 5 lines)
- Reading existing files for reference
- Adding to kustomization.yaml resources list

## Workflow

### 1. Research (Delegate to Subagent)

Research before editing. Prefer current repo patterns first, then kubesearch or named upstream repos.

1. Inspect nearby apps in `kubernetes/apps/<namespace>/` using app-template, especially apps with similar route, persistence, auth, and storage needs.
2. If the user names an upstream pattern such as `onedr0p/home-ops`, preserve that constraint and adapt only relevant values.
3. Preserve user constraints verbatim in research context, including auth, route exposure, persistence, namespace, and urgency.

```
Spawn subagent with:
- App name
- Preferred namespace (if mentioned)
- User constraints verbatim
- Task: Search current repo patterns first, then kubesearch.dev / named upstream FluxCD references
- Return: Best reference URL + key values to adapt
```

**Subagent context (minimal):**

```yaml
app_name: "<app-name>"
namespace: "<namespace>"  # or ask user
constraints: "<copy user constraints verbatim>"
output: Find repo/upstream references + key configuration values
```

### 2. Ensure Isolated Worktree Before Edits

Before editing files, use the `git-worktree-isolation` skill unless already operating in a task-specific isolated worktree.

Do not hand-roll `git worktree` commands here. Do not use `cd` in bash examples; use the Bash tool `workdir` parameter.

If the user only asked for evaluation/review and not edits, do not create a worktree.

### 3. Determine Namespace

Choose based on app purpose:

| Namespace | Purpose |
|-----------|---------|
| `default` | General apps |
| `network` | Proxies, gateways |
| `observability` | Monitoring, metrics |
| `media` | Media servers |
| `database` | Databases |
| `security` | Security tools |

### 4. Create Structure

```
kubernetes/apps/<namespace>/<app-name>/
├── ks.yaml              # Flux Kustomization
└── app/
    ├── helmrelease.yaml
    └── kustomization.yaml
```

### 5. Files

**Secrets:**

- Never commit plaintext secret values or `age.key`.
- Use SOPS for Kubernetes `Secret` resources: `secret.sops.yaml`.
- If secrets are required but values are unknown, create the secret manifest structure only after the user approves secret handling; do not invent or expose values.
- If auth is disabled by explicit user instruction, keep the route internal-only unless the user explicitly approves broader exposure.

**ks.yaml:**

If the app has persistent config/data that should be backed up, follow the existing VolSync pattern:

- `metadata.name: &app <app-name>`
- add `components: - ../../../../components/volsync`
- add `postBuild.substitute.APP: *app`
- set `VOLSYNC_CAPACITY`
- use `existingClaim: *app` in HelmRelease persistence

```yaml
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: <app-name>
  namespace: &namespace <namespace>
spec:
  targetNamespace: *namespace
  interval: 30m
  path: ./kubernetes/apps/<namespace>/<app-name>/app
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  wait: false
```

**app/kustomization.yaml:**

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: <namespace>

resources:
  - ./helmrelease.yaml
```

**app/helmrelease.yaml** (app-template):

Do not blindly use this skeleton. First copy the closest existing app-template pattern from the target namespace and adapt it. For media apps, inspect `radarr`, `sonarr`, `qbittorrent`, `seerr`, `wizarr`, or similar apps.

Rules:

- Include the app-template schema comment used by existing HelmReleases.
- Prefer `metadata.name: &app <app-name>` and reuse `*app` for controller/service/persistence.
- Do not set `strategy: RollingUpdate` when using RWO/Ceph-backed persistence; omit strategy unless the app is stateless or RollingUpdate is known-safe.
- For persistent config needing backup, use the repo VolSync pattern in `ks.yaml` and `persistence.<name>.existingClaim: *app`.
- Use `${SECRET_DOMAIN}` and `${TIMEZONE}` placeholders where appropriate.
- If disabling app auth, keep the route on `envoy-internal` unless the user explicitly asks for external exposure.

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/bjw-s-labs/helm-charts/main/charts/other/app-template/schemas/helmrelease-helm-v2.schema.json
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: &app <app-name>
spec:
  interval: 30m
  chartRef:
    kind: OCIRepository
    name: app-template

  values:
    controllers:
      <app-name>:
        replicas: 1

        annotations:
          reloader.stakater.com/auto: "true"

        pod:
          securityContext:
            runAsUser: 1000
            runAsGroup: 1000
            fsGroup: 1000
            fsGroupChangePolicy: "OnRootMismatch"

        containers:
          app:
            image:
              repository: <image-repo>
              tag: <tag>
            resources:
              requests:
                cpu: 5m
                memory: 16Mi
              limits:
                cpu: 500m
                memory: 512Mi

    service:
      app:
        controller: <app-name>
        ports:
          http:
            port: <port>

    route:
      app:
        hostnames:
          - "{{ .Release.Name }}.${SECRET_DOMAIN}"
        parentRefs:
          - name: envoy-internal
            namespace: network
```

### 6. Update Namespace Kustomization

Add to `kubernetes/apps/<namespace>/kustomization.yaml`:

```yaml
resources:
  - ./<app-name>/ks.yaml
```

### 7. Validate

```bash
mise exec -- kubeconform -strict kubernetes/
```

If scripts changed, also run:

```bash
mise exec -- shellcheck scripts/*.sh
```

Do not run live cluster reconciliation unless the user explicitly approves it.

### 8. Stop Before Commit/PR Unless Requested

After validation, summarize changed files and validation results.

Only commit, push, or create a PR if the user explicitly requested it.

Before any commit, inspect `git status`, `git diff`, and `git log --oneline -10`. Before any push, ask for confirmation.

## Anti-Patterns

**DON'T:**

- Deploy without checking kubesearch.dev for existing configs
- Skip validation before PR
- Use `cd` + commands in bash tool (use `workdir` param)
- Create new namespace without confirming with user
- Hardcode domains (use `${SECRET_DOMAIN}`)
- Commit, push, create PRs, or reconcile the live cluster without explicit approval

## Quick Reference

| Task | Command |
|------|---------|
| Validate | `mise exec -- kubeconform -strict kubernetes/` |
| Reconcile | Ask first, then `mise exec -- flux reconcile kustomization <name> -n <namespace>` |
| Check logs | `mise exec -- kubectl logs -n <ns> deployment/<app>` |

## Related

- git-worktree-isolation skill
- flux-operator AGENTS.md
