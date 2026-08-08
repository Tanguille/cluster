# Phase 7: LLM / Agent Attack Surface
**Completed:** 2026-08-06

## Threat Model Summary

The cluster runs two autonomous agents (Hermes, OpenCode) and a shared MCP tool
gateway (`vmcp-unified`) that aggregates 8 backends. The aggregate tool surface reachable
through that one gateway includes: a **cluster kubeconfig** (flux-operator MCP), a **Talos
`os:reader` credential** (talos-mcp), a **GitHub PAT** with repo/issue/PR write on
`tanguille/cluster` (github MCP entry), **Home Assistant device control** with a long-lived HA
token, **Grafana alert-rule and dashboard write**, **arbitrary web fetch** (searxng, context7,
karakeep `get_bookmark_content`), and **Karakeep bookmark write**. That single gateway declares
`incomingAuth: type: anonymous`
(`kubernetes/apps/ai/toolhive/config/virtualmcpservers.yaml:68-69`) and is additionally reachable
over a LAN HTTPRoute at `mcp.${SECRET_DOMAIN}`
(`kubernetes/apps/ai/toolhive/app/httproute.yaml:8-17`). There are **2 CiliumNetworkPolicies in the
entire repo** (`kubernetes/apps/kube-system/network-policies/app/`), neither of which touches the
`ai` namespace — so any pod anywhere in the cluster, and any host on the LAN, can drive that
tool set with no credential at all.

The shortest path from injected text to real damage: an agent reads attacker-controlled content
(a fetched web page, a Karakeep bookmark, a GitHub issue body, an HA entity `friendly_name`, a
Discord message), the injected instruction causes one `call_tool` to the anonymous vmcp gateway,
and the payload is either exfiltrated over unrestricted egress or written back into a location the
agent re-reads later. No egress policy exists anywhere in `ai`, so exfiltration is a single
`web_fetch`-style call. Persistence is worse than exfiltration here: Hermes' entire behavioural
config — `config.yaml`, cron definitions, skills, plugins, and a `.env` holding the Discord bot
token — lives on the `hermes` PVC at `/opt/data`, is writable by the agent process, and is **not in
git** (`docs/hermes-config.md:1-5`). An injection that appends a cron entry is a standing backdoor
that survives restarts, is invisible to Flux, and has no drift detection.

## Attack Chains

Ranked by likelihood × impact.

### 1. Untrusted page → Hermes browser (`--no-sandbox`, same container as secrets + PVC) → PVC cron persistence
**Likelihood: high. Impact: high.**
Hermes runs Chromium **inside the agent container** with
`AGENT_BROWSER_ARGS: "--no-sandbox,--disable-dev-shm-usage"`
(`kubernetes/apps/ai/hermes/app/helmrelease.yaml:55`). The repo's own analysis states the
consequence plainly: "`--no-sandbox` gives a compromised Chromium renderer the same reach as the
hermes process itself — `hermes-secret`, `/opt/data`, and the network"
(`docs/hermes-browser-sidecar-isolation-plan.md:5-8`). The fix (a separate `chrome` controller, the
pattern karakeep already uses at `kubernetes/apps/default/karakeep/app/helmrelease.yaml:87-95`) is
**written but not merged** — no sidecar/controller exists in the live HelmRelease. This is both a
renderer-RCE chain and a prompt-injection chain: either way the endpoint is `/opt/data`, where
`config.yaml`, cron, skills, plugins and `.env` (Discord bot token) live. Writing there is
persistence Flux cannot see or revert.

### 2. Any LAN host or any cluster pod → anonymous vmcp gateway → kubeconfig / HA / GitHub PAT
**Likelihood: high (no attacker sophistication needed). Impact: critical.**
`incomingAuth: type: anonymous` (`virtualmcpservers.yaml:68-69`) plus an HTTPRoute on
`envoy-internal` (`httproute.yaml:8-17`) plus zero CNPs in `ai` means the confused deputy needs no
deputy — the caller *is* the attacker. One `call_tool` reaches
`toolhive-secrets/KUBECONFIG` mounted into flux-operator MCP
(`kubernetes/apps/ai/toolhive/config/flux-operator.yaml:20-24`), or the HA token
(`homeassistant.yaml:20-23`), or the GitHub PAT injected server-side by `headerForward`
(`github.yaml:19-24`). The PAT is never shown to the caller but is *used* on their behalf: the
attacker gets GitHub writes to the cluster repo without ever seeing the token. Any pod on the
cluster and any device on the LAN qualifies — including a compromised IoT device, or a media app
in `default`/`media` that got popped through the internet-facing gateway.

### 3. GitHub issue/PR body → PR-review runner → `os:admin` Talos credential
**Likelihood: medium. Impact: critical.**
`kubernetes/apps/actions-runner-system/actions-runner-controller/runners/cluster/rbac.yaml:15`
grants the runner Talos `roles: ["os:admin"]`, and that credential is mounted into every runner pod
at `/var/run/secrets/talos.dev`
(`runners/cluster/helmrelease.yaml:51-54, 65-68`). `ACTIONS_RUNNER_REQUIRE_JOB_CONTAINER: "false"`
(line 35-36) means workflow steps run directly in the runner container with that mount. The repo
runs an LLM PR reviewer over untrusted PR content. `os:admin` on Talos is node-level: it can read
machine config (which contains cluster CA material) and reboot/reset nodes. The comment says the
only use is `talosctl image pull` — the credential's scope is far wider than its use.

### 4. Karakeep bookmark / HA entity name → Hermes agent turn → exfil over unrestricted egress
**Likelihood: medium-high. Impact: medium-high.**
`docs/hermes-config.md` documents `platforms.homeassistant.extra.watch_entities` firing "a full
agent turn per state change — roughly a 74K-token prompt each". Entity attributes (device names,
media titles, notification bodies) are attacker-influenceable via any device on the network. Home
Assistant itself is exposed to the internet
(`kubernetes/apps/network/external-service/homeassistant/httproute.yaml:21-25`). Same class:
`get_bookmark_content` returns whatever a saved page said. With no egress policy in `ai`, the
resulting turn can POST anything it has in context to any host.

### 5. Anything → OpenCode → its ServiceAccount token
**Likelihood: medium. Impact: depends on RBAC (unverifiable from git).**
OpenCode is an agentic coding assistant with shell and file tools; it explicitly opts *in* to
`automountServiceAccountToken: true`
(`kubernetes/apps/ai/opencode/app/helmrelease.yaml:62`) while Hermes correctly sets it to `false`
(`hermes/app/helmrelease.yaml:15`). No ServiceAccount name is set, so it gets the `ai` namespace
`default` SA. No RoleBinding for `default` in `ai` exists in the repo, so the token is probably
near-useless — but it is still a mounted bearer token an injected instruction can read and
exfiltrate, and it opens a `/api` path to the apiserver for enumeration. The `ai` namespace is
**not** covered by `deny-kube-apiserver-egress`, which only targets `media`, `default`, `web3`
(`deny-apiserver-egress.yaml:14, 35, 56`).

### 6. Cross-namespace: compromised agent → databases
**Likelihood: medium. Impact: medium.**
`database-ingress.yaml:17-19` explicitly allowlists `io.kubernetes.pod.namespace: ai` to the whole
`database` namespace — postgres, pgbouncer, dragonfly. Necessary (litellm, memini, vmcp session
store all live there) but it means an agent-pod compromise reaches every database in the cluster,
not just its own.

## Agent Tool & Credential Inventory

| Agent/pod | Tools/capabilities | Secrets mounted | SA token automounted? | Egress restricted? | K8s API access? |
|---|---|---|---|---|---|
| `hermes` (ai) | in-container Chromium (`--no-sandbox`), full vmcp tool set via MCP, Discord + Home Assistant platforms, cron scheduler, PVC read/write at `/opt/data` | `hermes-secret` (dashboard basic-auth only) via `envFrom`; **real creds — Discord bot token, provider keys — live in `/opt/data/.env` on the PVC, not SOPS** | No (`:15` `false`) — correct | **No** | Not via SA; yes indirectly via flux-operator MCP kubeconfig |
| `opencode` (ai) | coding agent w/ shell+file tools, remote MCP → `vmcp-unified`, 3 npm plugins incl. `@tarquinen/opencode-dcp@latest` (unpinned) | `LITELLM_API_KEY` via env | **Yes** (`:62` explicit `true`) | **No** | Yes (default SA, RBAC unverified) |
| `vmcp-unified` (ai) | aggregates all 8 MCP backends; `find_tool` + `call_tool` | `${LITELLM_API_KEY}` as `OPENAI_API_KEY` | Operator-managed (unverifiable from git) | **No** | Via backends |
| `flux-operator` MCP | Flux/K8s reads+writes | **`toolhive-secrets/KUBECONFIG`** mounted at `/kubeconfig` | n/a | **No** | **Yes — full kubeconfig, scope unverifiable from git** |
| `talos-mcp` | Talos node queries | `talos-mcp` SA secret, `os:reader`, `TALOS_MCP_READ_ONLY: "true"` | n/a | `permissionProfile: network` | Talos API |
| `homeassistant` MCP | **device control** on the real HA instance | `HOMEASSISTANT_API_TOKEN` | n/a | **No** | No |
| `github` MCP entry | 46 repo/issue/PR tools incl. writes | `GITHUB_PERSONAL_ACCESS_TOKEN` header-forwarded server-side | n/a | Remote `api.githubcopilot.com` | No |
| `grafana` MCP | `create/update/delete_alert_rule`, `update_dashboard`, `create_annotation` | anonymous to in-cluster Grafana | n/a | **No** | No |
| `searxng` / `context7` / `karakeep` / `kubesearch` MCP | web search, doc fetch, bookmark read+write, cluster-manifest search | `KARAKEEP_API_KEY`, `CONTEXT7_API_KEY` | n/a | **No** | No |
| `cluster-runner` (actions-runner-system) | arbitrary workflow shell, LLM PR review over untrusted PR text | **Talos `os:admin`** at `/var/run/secrets/talos.dev`, GitHub app creds | Yes (`cluster-runner` SA) | **No** | Yes |

## Re-opened Accepted Decisions

| Decision | Original rationale | Cost under this threat model | Proportionate mitigation | Still worth keeping? |
|---|---|---|---|---|
| `hermes-agent:main` rolling tag | availability / auto-update | **Already fixed — the premise is stale.** `hermes/app/helmrelease.yaml:31-32` pins `tag: v2026.8.3@sha256:1678831…`. Nothing to re-open. | none needed; keep Renovate driving the digest | Yes — keep as-is |
| CrowdSec `failOpen: true` | "Bouncer/appsec outage must not 403 the whole gateway; crowdsec is defense-in-depth" (`envoy.yaml:253-255`) | Low-moderate here. Only `nextcloud`, `picoshare`, `jellyfin`, `seerr`, `wizarr`, `gatus`, `kromgo`, `flux` and **homeassistant** sit on `envoy-external`; no agent surface does. The real cost is on chain 4 (HA is internet-facing and its entities feed Hermes). | Don't flip it to fail-closed — the availability argument holds and the alert already exists (`crowdsec/app/prometheusrule.yaml:13-17`). Instead give the AppSec deployment a PDB + a second replica so "under pressure" stops meaning "zero pods". | Keep `failOpen`; fix the availability of the thing it fails open on |
| No `bodyToExtAuth` (413s on nextcloud) | body inspection broke nextcloud uploads | Real but *misdirected* cost. The injection bodies that matter here are not HTTP request bodies arriving through the external gateway — they are **response** bodies fetched outbound by the agent (web pages, bookmarks, GitHub issues). CrowdSec inspects neither direction of that, so `bodyToExtAuth` would not close chain 1, 3 or 4 even if enabled. | Not worth re-litigating for agent safety. If it's wanted for nextcloud's own sake, scope a second SecurityPolicy with `bodyToExtAuth` to the *non-nextcloud* hostnames only rather than the whole gateway. | Keep disabled — it's the wrong control for this threat |
| 2 CNPs, Hubble disabled, generator declined | generator was 2.5Gi for a one-shot job | **This is the load-bearing gap.** With no egress policy in `ai`, every chain above ends in "and then it POSTs to the attacker". | Don't generate policies. Write **one** hand-written CNP for `ai` — see Win #2. ~40 lines, no tooling, no Hubble. | Gap must close; the generator rejection was still correct |
| Unverified/unpinned OCI + MCP images | convenience | Actually good: every MCP image in `toolhive/config/` is digest-pinned, including `kubesearch-mcp:master@sha256:…` (mutable tag, but the digest is what's pulled, so it's fine). The real unpinned supply chain is **OpenCode's npm plugins**: `"@tarquinen/opencode-dcp@latest"` (`opencode.jsonc:6`) resolves at runtime, in-process, in a pod with an SA token. | Change `@latest` to a pinned version in `opencode.jsonc:6`. One-word diff. | Images: keep. npm `@latest`: fix |

## Vulnerabilities Found

| Severity | Category | Resource | Location | Description | Remediation |
|---|---|---|---|---|---|
| Critical | Confused deputy / authn | `VirtualMCPServer/unified` | `kubernetes/apps/ai/toolhive/config/virtualmcpservers.yaml:68-69` | `incomingAuth: type: anonymous` on a gateway fronting a cluster kubeconfig, a GitHub PAT, an HA control token and Talos access. Reachable from every pod in the cluster and, via `mcp.${SECRET_DOMAIN}` on `envoy-internal`, from every host on the LAN. | Switch `incomingAuth` off anonymous (toolhive supports token/OIDC modes), or — cheaper — add a CNP restricting ingress to `vmcp-unified` to the hermes/opencode endpoints only, and drop the HTTPRoute if the LAN entry point isn't used |
| Critical | Egress / exfiltration | whole `ai` namespace | no file — absence of a CNP | Zero egress policy. Every agent and every MCP backend can reach any internet host. Turns any successful injection into a working exfiltration channel. | See Cheapest Win #2 |
| High | Sandbox escape / blast radius | `hermes` container | `kubernetes/apps/ai/hermes/app/helmrelease.yaml:55` | Chromium runs `--no-sandbox` **in the agent container**, sharing `hermes-secret`, `/opt/data` (config, cron, skills, plugins, Discord token) and the network. The repo's own doc calls this out; the fix is written but unmerged. | Merge `docs/hermes-browser-sidecar-isolation-plan.md` — separate `chrome` controller, same shape as `karakeep/app/helmrelease.yaml:87-95` |
| High | Credential reach | `cluster-runner` | `.../runners/cluster/rbac.yaml:15` + `helmrelease.yaml:35-36,51-54` | Talos `os:admin` mounted into runner pods that execute untrusted PR workflow content directly (no job container). Comment says the only use is `talosctl image pull`. | Mount the Talos secret only on the job(s) that pull images, not on the pod template; or gate the LLM-review workflow to a runner scale set without the mount |
| High | Persistence | `hermes` PVC | `docs/hermes-config.md:1-5` | Agent's own config, cron, skills and plugins are agent-writable, off-git, undetected by Flux. Injection → cron entry → standing backdoor. | Add a periodic `kubectl exec` diff of `/opt/data/config.yaml` + cron into a git-tracked reference copy, or mount the config subpath read-only if Hermes tolerates it |
| Medium | Credential exposure | `opencode` | `kubernetes/apps/ai/opencode/app/helmrelease.yaml:62` | `automountServiceAccountToken: true` on an agent with shell tools, while hermes correctly sets `false`. | Set to `false` unless OpenCode genuinely needs the API; nothing in the config suggests it does |
| Medium | Supply chain | `opencode` plugins | `kubernetes/apps/ai/opencode/app/config/opencode.jsonc:6` | `"@tarquinen/opencode-dcp@latest"` — unpinned third-party npm plugin executed in-process in an agent pod. `cc-safety-net` and `oh-my-opencode-slim` are also unversioned. | Pin all three to explicit versions |
| Medium | Lateral movement | `ai` → `database` | `.../network-policies/app/database-ingress.yaml:17-19` | Whole `ai` namespace allowlisted to the whole `database` namespace. Necessary but over-broad. | Narrow to the pods that need it (`litellm`, `memini`, `vmcp-unified`) via `matchLabels` on the endpoint, not just namespace |
| Medium | Authn | `omniroute` | `kubernetes/apps/ai/omniroute/app/helmrelease.yaml:31` | `REQUIRE_API_KEY: "false"` — any pod can drive the LLM gateway and burn provider quota / use it as an outbound proxy | Accepted per the inline comment; low priority given internal-only route, but worth an ingress CNP alongside Win #2 |
| Low | K8s API egress | `ai` namespace | `.../deny-apiserver-egress.yaml:14,35,56` | The apiserver-egress deny covers `media`, `default`, `web3` — not `ai`, which holds the agents. | Add an `ai` stanza with the same `egress.home.arpa/kube-apiserver: allow` opt-out label; toolhive/litellm operators would need the label |
| Low | Admission | all namespaces | `kubernetes/components/common/namespace.yaml:9-10` | PSS is `warn` + `audit` at `baseline`; no `enforce`. Nothing blocks a privileged agent pod. | Add `pod-security.kubernetes.io/enforce: baseline` once the audit log is clean (generic — likely covered by the parallel CIS audit) |

## Cheapest High-Impact Wins

1. **Authenticate or firewall `vmcp-unified`.** The single largest gap. Cheapest version if
   toolhive's non-anonymous auth is fiddly: a CNP in `ai` with
   `endpointSelector: {toolhive.stacklok.dev/name: unified}` and an ingress rule listing only the
   hermes and opencode endpoints (plus `network` for the gateway if the LAN route is kept). ~15
   lines in a new file under `kubernetes/apps/kube-system/network-policies/app/`. Also consider
   deleting `kubernetes/apps/ai/toolhive/app/httproute.yaml` entirely if nothing outside the
   cluster uses `mcp.${SECRET_DOMAIN}`.

2. **One egress CNP for `ai`.** Same directory, ~40 lines. Default-deny egress for the agent pods
   only (`hermes`, `opencode`), with explicit allows for: `kube-dns`; the `ai` namespace itself
   (litellm, vmcp); `database`; and `toFQDNs` for the handful of hosts they legitimately need
   (`api.githubcopilot.com`, `mcp.context7.com`, `discord.com`, provider APIs). Cilium's `toFQDNs`
   makes this tractable without Hubble and without a policy generator. Start with
   `enableDefaultDeny: egress: false` + `egressDeny` to `toEntities: [world]` minus the allows if a
   full default-deny is too risky to land at once.

3. **Merge the browser sidecar plan.** `docs/hermes-browser-sidecar-isolation-plan.md` is complete,
   the pattern is already proven in-cluster by karakeep, and it removes the secret + PVC from the
   blast radius of every browsed page. This is written work sitting unmerged.

4. **Two one-line diffs.** `opencode/app/helmrelease.yaml:62` → `automountServiceAccountToken:
   false`. `opencode/app/config/opencode.jsonc:6` → pin `@tarquinen/opencode-dcp` to a version.
   Minutes of work, removes a mounted bearer token and a runtime-resolved dependency from an agent
   pod.

5. **Unmount Talos `os:admin` from the general runner pod template.** Move the volume+mount from
   `runners/cluster/helmrelease.yaml:51-54,65-68` onto only the workflow that runs `talosctl image
   pull`, so untrusted-PR review jobs don't carry node-admin credentials.

## Unauditable From Git

- **Hermes' entire behaviour.** `config.yaml`, cron definitions, skills, plugins, enabled tools,
  Discord/HA platform wiring, and `/opt/data/.env` (Discord bot token, provider keys) live on the
  `hermes` PVC. `docs/hermes-config.md:1-5` states this explicitly. Which tools Hermes can actually
  call, what its cron jobs do, and what `watch_entities` it subscribes to are all invisible to this
  review and to Flux. **This is itself the single most important structural finding**: the highest-
  privilege agent in the cluster has no configuration-drift detection.
- **What `toolhive-secrets/KUBECONFIG` can actually do.** The SOPS blob confirms the key exists;
  the RBAC it carries is not in the repo. Could be a read-only SA, could be cluster-admin.
- **OpenCode's effective RBAC.** It mounts the `ai` `default` SA token; no RoleBinding for it
  appears in the repo, but bindings could exist outside `kubernetes/apps/ai/`.
- **The GitHub PAT's scopes.** `github.yaml:1-8` says a token without `copilot` scope lists 46
  repo/issue/PR tools; the actual scopes granted are not recorded anywhere in git.
- **vmcp backend tool descriptions.** MCP tool *descriptions* are served by the backends at
  runtime and land directly in agent context. A compromised or updated upstream image (context7,
  github remote) can change them without any repo change.
- **CrowdSec AppSec replica count / PDB.** Not set in
  `kubernetes/apps/security/crowdsec/app/helmrelease.yaml:159-173`, so whether `failOpen` is a
  frequent or rare condition depends on chart defaults, not on this repo.

## Summary Stats
- Total issues: 11
- Critical: 2 | High: 3 | Medium: 4 | Low: 2
