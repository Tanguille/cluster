# k8s-at-home research workflow

## Query recipes

Start broad, then narrow by resource type. Use exact code-shaped queries rather than broad prose.

### Tool preference

1. Use **kubesearch MCP** first — it queries pre-indexed kubesearch.dev data without needing GitHub API rate limit:
   - `kubesearch_search_releases` — find apps by chart name, ranked by deployment count.
   - `kubesearch_get_release` — drill into a chart for every deployment and its `spec.values`.
   - `kubesearch_grep_values` — full-text grep across real-world Helm values for config examples.
   - `kubesearch_search_images` — find container image repositories and tags.
   - Then `repo_clone`/`repo_read_file`/`repo_grep` to review exemplar manifests from indexed repos.
2. Fall back to **GitHub MCP** repository search and file reads for repos not yet indexed by kubesearch.
3. Use `gh search repos <term> --topic k8s-at-home` when MCP is unavailable or when you need a quick shell-native shortlist.
4. Use `gh search code` or `grep_app_searchGitHub` for code patterns when MCP code search fails.

Keep discovery bounded: request 5–10 repositories, inspect 3–5 examples, and only broaden if evidence is weak.

### Kubesearch query patterns (preferred)

```
# Find who deploys your chart, ranked by popularity
kubesearch_search_releases(query: "cert-manager", limit: 10)
# Drill into deployments + spec.values; response includes repo file_url
kubesearch_get_release(id: "<id>", limit: 10)
# Grep config examples across all indexed Helm values
kubesearch_grep_values(query: "storageClass: ceph-block")
# Find container image repos and tags
kubesearch_search_images(query: "nginx")
# After finding a repo, clone and review manifests
repo_clone(repo: "owner/repo")  → repo_read_file / repo_grep
```

### Repository discovery (fallback)

```text
topic:k8s-at-home <app>
topic:k8s-at-home homelab flux <app>
topic:k8s-at-home <chart-name>
```

GitHub CLI equivalent:

```bash
gh search repos <app> --topic k8s-at-home --limit 10 --json fullName,url,updatedAt,stargazersCount,description
```

Bundled script (from repo root):

```bash
python3 .agents/skills/k8s-at-home-research/scripts/search_k8s_at_home.py <app> --limit 10
```

### Path narrowing

After you have promising repos, scope code search:

```text
repo:<owner>/<repo> "kind: HelmRelease" "<app>"
repo:<owner>/<repo> path:kubernetes/apps "<app>"
repo:<owner>/<repo> path:helmrelease.yaml "<app>"
repo:<owner>/<repo> path:ks.yaml "<app>"
repo:<owner>/<repo> path:kustomization.yaml "<app>"
```

### Application examples

```text
"kind: HelmRelease" "<app>" topic:k8s-at-home
"<app>" "app-template" topic:k8s-at-home
"<app>" "bjw-s-labs" topic:k8s-at-home
"<app>" "chartRef:" "<chart-name>" topic:k8s-at-home
"<app>" "repository: <image-repo>" topic:k8s-at-home
"<app>" "external-secrets.io" topic:k8s-at-home
"<app>" "HTTPRoute" topic:k8s-at-home
```

### Persistence and backups

```text
"<app>" "PersistentVolumeClaim" topic:k8s-at-home
"<app>" "storageClassName" topic:k8s-at-home
"<app>" "ReplicationSource" topic:k8s-at-home
"<app>" "volsync.backube" topic:k8s-at-home
```

### Networking

```text
"<app>" "HTTPRoute" topic:k8s-at-home
"<app>" "Gateway" topic:k8s-at-home
"<app>" "Ingress" topic:k8s-at-home
"<app>" "external-dns" topic:k8s-at-home
```

### Observability and operations

```text
"<app>" "ServiceMonitor" topic:k8s-at-home
"<app>" "PrometheusRule" topic:k8s-at-home
"<app>" "reloader.stakater.com" topic:k8s-at-home
```

## Source quality ranking

Prefer examples that are:

1. Recent commits within the last 12–18 months.
2. Flux v2 resources (`helm.toolkit.fluxcd.io/v2`, `kustomize.toolkit.fluxcd.io/v1`).
3. Similar chart family to this cluster, especially `app-template` / `bjw-s-labs` style values.
4. Complete app folders with `ks.yaml`, `helmrelease.yaml`, `kustomization.yaml`, and secrets placeholders.
5. Explicit about persistence, probes, resources, and backup/restore behavior.

Treat examples as lower confidence if they use deprecated APIs, unmaintained chart repos, plaintext secrets, hardcoded domains, or custom CRDs not present in this cluster.

## Adaptation notes for this cluster

- Preserve this repo's GitOps layout and naming; do not mirror another repo's folder hierarchy unless it matches.
- Convert domains to `${SECRET_DOMAIN}`.
- Convert internal routes to Gateway API `HTTPRoute` with parentRef `envoy-internal` in namespace `network` when appropriate.
- Use SOPS-managed `Secret` or ExternalSecret-compatible patterns; never copy secret values.
- Add Reloader annotations to controllers that consume mutable config.
- For `ceph-block` RWO persistence, avoid RollingUpdate unless the workload supports multi-attach; use `Recreate` when in doubt.
- Validate chart values against upstream chart docs before relying on a copied values block.

## Evidence standard

Every recommendation should cite at least one repository path. When proposing a manifest change, cite the exact source pattern and explain the cluster-specific adaptation.
