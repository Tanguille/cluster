---
name: k8s-at-home-research
description: >-
  Research Kubernetes GitOps patterns from public GitHub repositories tagged `k8s-at-home`
  and adapt findings to this cluster. Use before adding or upgrading apps, writing
  HelmRelease/Kustomization/HTTPRoute/PVC/VolSync manifests, comparing chart values, or
  investigating how homelab clusters configure an application.

  user: "How do homelab clusters deploy X?" → Search topic repos, cite exemplar manifests
  user: "Find k8s-at-home examples for jellyfin" → Shortlist repos, compare HelmRelease patterns
  user: "Chart values for app-template like others use" → Code search scoped to top repos

  Prefer this before inventing Kubernetes YAML when the app may exist in the k8s-at-home ecosystem.
compatibility: Requires network access to GitHub (REST API, `gh`, or GitHub MCP). Optional GH_TOKEN/GITHUB_TOKEN; Python 3 for the bundled repo search script.
---

# k8s-at-home research

## When to use

- Real-world examples before authoring or upgrading cluster manifests.
- Comparing chart values, probes, persistence, routes, backups, or secrets patterns.
- User names an app and asks how k8s-at-home / homelab repos configure it.

Research is **advisory**: never copy secrets, domains, cluster-specific IDs, or unreviewed defaults verbatim.

## Workflow

1. **Define target** — app aliases; resource type (`HelmRelease`, `HTTPRoute`, `ReplicationSource`, …); chart/image; namespace.
2. **Discover repos** — `topic:k8s-at-home` shortlist (5–10), inspect top 3–5. Query recipes: [references/research-workflow.md](references/research-workflow.md).
3. **Compare exemplars** — chart/version, values, probes, securityContext, resources, persistence, routes, env, secrets, Flux intervals/deps/postBuild.
4. **Adapt to this cluster** — `kubernetes/apps/<namespace>/<app>/` with `ks.yaml`; `${SECRET_DOMAIN}`; internal `HTTPRoute` parentRef `envoy-internal` in `network`; Reloader when config-backed; `Recreate` for RWO `ceph-block`; SOPS only.
5. **Report with evidence** — cite repo/path per recommendation.

## Tool routing

- **GitHub MCP** first when available: `resources_github_search_repositories`, `resources_github_get_file_contents`.
- **Fallback:** `gh search repos <app> --topic k8s-at-home --limit 10` then scoped `gh search code` with `repo:<owner>/<repo>`.
- **Repo shortlist:** `python3 .agents/skills/k8s-at-home-research/scripts/search_k8s_at_home.py <term> --limit 10`
- **Literal code patterns:** `grep_app_searchGitHub` when MCP code search fails.
- **Web search** only to find repo names; confirm in repository files.
- **Local repo** — compare against existing `kubernetes/apps/` before proposing YAML.
- **`@explorer`** for local repo search only; **`@librarian`** for upstream chart docs, not k8s-at-home mining.

## Efficiency

- Bound discovery (5–10 repos, 3–5 deep reads); scope code search with `repo:` after the shortlist.
- Prefer in-session GitHub MCP repo search before shell when available.

## Evaluation and output

Per source: relevance, reuse, reject (hardcoded domains, plaintext secrets, deprecated APIs, wrong storage/ingress), cluster adaptation. Quality ranking and adaptation rules: [references/research-workflow.md](references/research-workflow.md).

Unless the user asks for raw dumps, use:

```markdown
## k8s-at-home research: <target>

### Best references
1. <repo/path> — <why>

### Patterns to reuse
- <pattern> — source: <repo/path>

### Adaptation for this cluster
- <change>

### Risks / mismatches
- <item>

### Recommendation
<next step>
```

## Scripts

- [scripts/search_k8s_at_home.py](scripts/search_k8s_at_home.py) — GitHub repo search with `topic:k8s-at-home`.

Format reference: [agentskills.io](https://agentskills.io/specification).
