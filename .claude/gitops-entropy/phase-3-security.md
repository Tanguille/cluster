# Phase 3: Security & RBAC Review

**Completed:** 2026-08-06
**Scope:** `kubernetes/` `bootstrap/` `talos/` `.github/` `scripts/` `.taskfiles/` + root dotfiles (671 tracked files). `.worktrees/`, `.venv/`, `archive/`, `.vllm-opt/` excluded.

## Findings

### 1. RBAC

**Clean.** There is no `cluster-admin` binding anywhere in git, no wildcard (`"*"`) verb/resource/apiGroup rule, and no binding to `system:authenticated`, `system:unauthenticated`, or `system:masters`. Only two RBAC manifests exist in the repo:

- `kubernetes/apps/flux-system/flux-operator/app/clusterrolebinding.yaml` — binds Group `flux-admin` to ClusterRole `flux-web-admin` (chart-supplied, scoped to Flux CRs). Justified.
- `kubernetes/apps/ai/litellm/app/rbac.yaml` — additive ClusterRole for `litellmteams`/`litellmvirtualkeys` only, bound to the `litellm-operator` SA. Narrow, correct, and the comment names the upstream gap it patches. Justified.

Inline chart RBAC is likewise tight. `kubernetes/apps/default/homepage/app/helmrelease.yaml:91-107` explicitly declares its ClusterRole rather than taking a chart default, and grants only `get,list` on namespaces/pods/nodes/services/ingresses/httproutes/gateways/metrics — no secrets. This is a model least-privilege declaration.

**The runners do NOT have Kubernetes cluster-admin** — the previous note holds, verified in git. `kubernetes/apps/actions-runner-system/actions-runner-controller/runners/cluster/rbac.yaml` creates a bare `cluster-runner` ServiceAccount with **zero RoleBindings**.

They do, however, hold a **Talos** credential — see finding V-01.

### 2. Pod security / privilege

**Pod Security Admission is not enforced anywhere.** Two independent facts combine:

`talos/patches/controller/cluster.yaml:3-5`
```yaml
  apiServer:
    admissionControl:
      $$patch: delete
```
This deletes Talos's shipped `PodSecurityConfiguration`, which by default enforces `baseline` cluster-wide with a `kube-system` exemption. With it gone, the apiserver has no PSA default.

`kubernetes/components/common/namespace.yaml:9-10`
```yaml
    pod-security.kubernetes.io/warn: baseline
    pod-security.kubernetes.io/audit: baseline
```
This component is applied by all 16 app-group kustomizations (verified: every `kubernetes/apps/*/kustomization.yaml` lists `components/common`), so every namespace gets **warn and audit but never `enforce`**. Net effect: any workload in any namespace can run privileged, hostPath, or hostNetwork and the apiserver will admit it with a log line.

**hostPath inventory (complete, 4 mounts):**

| Path | Workload | Mode | Assessment |
|---|---|---|---|
| `/sys` | `amdgpu-undervolt` DaemonSet, `kubernetes/apps/kube-system/amdgpu-undervolt.yaml:163-168` | **writable** | Functionally required (writes `power1_cap`), but paired with `privileged: true` + `allowPrivilegeEscalation: true` — see V-07 |
| `/dev` | `generic-device-plugin`, `kubernetes/apps/kube-system/generic-device-plugin/app/helmrelease.yaml:70-73` | writable | Standard device-plugin requirement for `/dev/dri` + `/dev/kfd` |
| `/var/lib/kubelet/device-plugins` | same, `:75-78` | writable | Required — this is the device-plugin registration socket dir |
| `/var/mnt/merged/` | `jellyfin`, `kubernetes/apps/media/jellyfin/app/helmrelease.yaml:114-116` | writable | Media library only, not a sensitive host path |

No container/CRI socket is mounted anywhere. No `/`, `/etc`, or `/var/run` mount. This is a good result.

**Privileged containers (2):** both listed above, both in `kube-system`, both device/hardware controllers. No app-namespace workload is privileged.

**Host namespaces:** `hostPID` and `hostIPC` appear nowhere. `hostNetwork: true` on exactly one workload — `kubernetes/apps/default/obico/moonraker-obico/helmrelease.yaml:15` — which does at least run `runAsNonRoot: true`, uid/gid 1000, and `seccompProfile: RuntimeDefault`. `node-exporter` explicitly sets `hostNetwork: false`.

**Added capabilities:** none. Every `capabilities:` block found is a `drop: [ALL]`.

**`runAsUser: 0` (7 sites)** — each verified and each carries a documented reason:
- `media/fileflows:31,55` — entrypoint needs root then drops to 568; the comment cites #3569 where "simplifying" this to 568 crashlooped. Accepted.
- `default/nextcloud:126` — entrypoint starts root, drops to www-data; `podSecurityContext.fsGroup: 33`. Accepted.
- `media/qbittorrent/tools/qbitrr:38` — init container only, writes `/config`. Fine.
- `ai/llmkube/models/*.yaml` (4 files) — GPU model servers. `qwen36-27b-sglang.yaml:160` documents that it *no longer* gets `CAP_DAC_OVERRIDE`, so the intent is understood.
- `observability/exporters/klipper-exporter:43` — `runAsNonRoot: false` while the container itself sets `allowPrivilegeEscalation: false` + `drop: [ALL]` + `readOnlyRootFilesystem: true`. Low residual risk.

`allowPrivilegeEscalation: true` appears once, on the already-privileged `amdgpu-undervolt` — redundant with `privileged`, not an additional grant.

**`seccompProfile: RuntimeDefault`** is set in 20 of 109 HelmReleases. Some of the remaining 89 inherit it from chart defaults, but there is no enforcement making it universal (see V-02/V-03).

### 3. Network policy

**Exact count: 4 policy objects in 2 files.** Zero `CiliumClusterwideNetworkPolicy`. Zero vanilla `NetworkPolicy`.

| File | Objects | Namespace(s) |
|---|---|---|
| `kubernetes/apps/kube-system/network-policies/app/database-ingress.yaml` | 1 CNP `database-ingress-allowlist` | `database` |
| `kubernetes/apps/kube-system/network-policies/app/deny-apiserver-egress.yaml` | 3 CNP `deny-kube-apiserver-egress` | `media`, `default`, `web3` |

**The known DNS-rev-NAT bug is NOT present.** `database-ingress.yaml:34-36` explicitly allows `io.kubernetes.pod.namespace: kube-system` / `k8s-app: kube-dns` with a comment naming the exact failure mode. The three `deny-kube-apiserver-egress` policies set `enableDefaultDeny: {egress: false}` and use `egressDeny` only, so they are pure deny overlays that never touch DNS. Both patterns are correct.

**No overly-permissive policy exists** — no `toEntities: all` / `fromEntities: all`, and the one `endpointSelector: {}` (database) is a deliberate namespace-wide default-deny with a documented allowlist, which is the *good* use of that selector.

**Namespaces with zero policy (12 of 16), ranked by blast radius.** Note that `database` is the only namespace with *ingress* protection; `media`/`default`/`web3` only have an egress deny-overlay, so they are unrestricted as sources for everything below:

1. **`actions-runner-system`** — runner pods mount the Talos `os:admin` secret (V-01). Any pod in the cluster can open TCP to a runner pod.
2. **`network`** — `cloudflared` holds the tunnel credential; `envoy-external`/`envoy-internal` pods are the ingress data plane. Reachable pod-to-pod from every app, bypassing the gateway (and therefore bypassing the CrowdSec SecurityPolicy, which is attached at the Gateway).
3. **`flux-system`** — `source-controller` serves Git/OCI artifacts over unauthenticated in-cluster HTTP; `flux-instance` holds the GitHub token.
4. **`ai`** — `litellm` holds provider API keys, `toolhive`/`vmcp` is the MCP gateway, `hermes` is an autonomous agent. Also one of only three namespaces in the Talos API allowlist (`talos/patches/controller/kubernetes-talos-api-access.yaml:7`).
5. **`rook-ceph`** — mons/OSDs and the Ceph admin keyring; storage-plane compromise is data-plane compromise.
6. **`security`** — CrowdSec LAPI + bouncer API key.
7. **`observability`** — Grafana admin, VictoriaMetrics write endpoint (unauthenticated in-cluster).
8. **`kube-system`** — Cilium agent, CoreDNS.
9. **`cert-manager`** — ACME account key + issued cert private keys.
10. `kopiur-system`, `openebs-system`, `system-upgrade` — lower value but still unrestricted.

The practical shape of the gap: the five internet-facing apps live in `default` and `media`, and nothing prevents a compromised one from reaching items 1-9 above on any port.

### 4. Secrets

**No plaintext secret is committed.** 55 `kind: Secret` manifests exist; 54 are `*.sops.yaml`. The one exception is a false positive on inspection:

`kubernetes/apps/media/qbittorrent/tools/qbitrr/secret.yaml`
```yaml
stringData:
  SONARR_API_KEY: ${SONARR_API_KEY}
```
These are Flux `postBuild.substituteFrom` placeholders resolved from `cluster-secrets` at reconcile time, not values. Correct pattern, no leak.

**`.sops.yaml` coverage is complete for every path that currently holds encrypted files.** All 54 `*.sops.yaml` files live under `talos/`, `kubernetes/apps/**`, or `kubernetes/components/common` — matched by the two `creation_rules` regexes (`talos/.*\.sops\.ya?ml` and `(bootstrap|kubernetes)/.*\.sops\.ya?ml`). A `*.sops.yaml` created outside those trees would match no rule, but `sops -e` **errors** rather than emitting plaintext in that case, so this is fail-safe. Not a finding.

**`.gitignore` and tracking — verified clean.** `git ls-files | grep -E '(age\.key|kubeconfig|talosconfig)'` returns **no matches**. All three exist on disk in the repo root and are ignored:
- `.gitignore:6` `age.key*` — note the comment correctly explains this was widened from `/age.key` to catch `age.key.bak-x25519-only`, which is present on disk and *would* decrypt every SOPS file in this **public** repo. Good catch, already fixed.
- `.gitignore:21-22` `kubeconfig`, `talosconfig`.
- `talos/clusterconfig/` (rendered machine configs, which contain the cluster CA and etcd keys) is ignored via its own `talos/clusterconfig/.gitignore`; only that `.gitignore` is tracked.

`gitleaks` runs on every push and PR (`.github/workflows/gitleaks.yaml`), with `persist-credentials: false` and a sha-pinned action.

**No ConfigMap contains a credential.** Sensitive values in ConfigMaps are all `${VAR}` placeholders.

### 5. Supply chain

**No admission-time policy engine exists.** Zero Kyverno `ClusterPolicy`, zero Gatekeeper `ConstraintTemplate`, zero `ValidatingAdmissionPolicy`. Combined with PSA being unenforced (§2), *nothing* in the cluster rejects a privileged pod, a hostPath mount, or a workload attaching itself to the internet gateway. Every control in this report is convention-only.

**No cosign verification.** 152 `OCIRepository` objects, `grep -rn 'verify:' kubernetes` returns **zero hits**. Flux pulls every chart from `ghcr.io/home-operations/charts-mirror` and friends with no signature check.

**Chart versions are all tag-pinned** (semver, Renovate-managed) — appropriate for OCI Helm charts.

**Container images not digest-pinned (9):**

| Image | Location |
|---|---|
| `ghcr.io/…/smtp-relay:5.1.0-alpine` | `kubernetes/apps/network/smtp-relay/app/helmrelease.yaml:21` |
| `steinbrueckri/envsubst:v2.1.7` | `kubernetes/apps/media/qbittorrent/tools/qbitrr/helmrelease.yaml:36` |
| qbitrr `:v5.12.12` | same file `:58` |
| homepage `:v1.13.2` | `kubernetes/apps/default/homepage/app/helmrelease.yaml:23` |
| gatus sidecar `:0.2.2` | `kubernetes/apps/observability/gatus/app/helmrelease.yaml:45` |
| postgres `:18` | `kubernetes/apps/default/immich/server/helmrelease.yaml:29` |
| obico `:sha-9b73caa…` ×2 | `kubernetes/apps/default/obico/app/helmrelease.yaml:19,55` |
| omniroute `:3.8.49` | `kubernetes/apps/ai/omniroute/app/helmrelease.yaml:22` |

`steinbrueckri/envsubst:v2.1.7` and `postgres:18` are the two that matter — the first is an unaudited personal Docker Hub account, the second is a floating major-version tag. Note the existing memory that ik_llama tags get rebuilt in place: mutable tags are a demonstrated hazard in this cluster.

Every other image in the repo is `tag@sha256:…`, including `latest@sha256:…` on generic-device-plugin (digest wins, so that one is fine).

### 6. Workload identity

`automountServiceAccountToken` is explicitly set on 4 workloads:
- `ai/hermes:15` → `false` (correct — Hermes executes tools; no token is right)
- `web3/xmrig-guard:54` → `false` (correct)
- `default/homepage:75` → `true` (justified — it reads nodes/httproutes for widgets, with the least-privilege ClusterRole above)
- `ai/opencode:62` → `true` (**not justified** — no `serviceAccount:` or `rbac:` block exists in `kubernetes/apps/ai/opencode/app/`, so this mounts a token for the `ai` namespace `default` SA, which has no bindings. Useless token on an internet-adjacent AI coding agent.)

`default/nextcloud:26-43` uses a JSON-patch to force `automountServiceAccountToken: false` on both the Deployment and the CronJob templates, with a comment explaining the chart offers no value for it. That is the right amount of effort for the highest-exposure app.

The remaining ~105 workloads inherit the cluster default (`true`). Most run under a namespace `default` SA with no bindings, so the token grants only `selfsubjectaccessreview` — low value. `media`, `default`, and `web3` additionally deny apiserver egress at the network layer, so their tokens are unusable. `ai`, `observability`, `network`, `security`, and `flux-system` have neither the mount disabled nor the egress denied.

**No long-lived cloud credential is mounted as a file.** Cloud tokens (Cloudflare, GitHub, provider API keys) are all `secretKeyRef` env vars from SOPS-managed Secrets.

### 7. Ingress exposure

**Gateways** (all in `kubernetes/apps/network/envoy-gateway/app/envoy.yaml`):

| Gateway | IP | Public DNS | Listeners |
|---|---|---|---|
| `envoy-external` | `${ENVOY_EXTERNAL_IP}` | `external.${SECRET_DOMAIN}` CNAME → cfargotunnel | HTTPS/443 `from: All`, HTTP/80 `from: Same` (301 redirect only) |
| `envoy-external-probe` | `${ENVOY_GATUS_PROBE_IP}` | `gatus-probe.${SECRET_DOMAIN}` CNAME → cfargotunnel | HTTPS/443, `from: Selector` → `observability` only |
| `envoy-internal` | `${ENVOY_INTERNAL_IP}` | none (see below) | HTTPS/443 `from: All`, HTTP/80 redirect |
| `envoy-internal-tls` | `${ENVOY_INTERNAL_TLS_IP}` | none | TLS/443 Passthrough, `from: Same` |

The internal gateways are **not** publicly resolvable: `kubernetes/apps/network/external-dns/app/helmrelease.yaml:33` sets `--gateway-name=envoy-external`, so external-dns publishes records for external-gateway routes only. The cloudflared wildcard (`hostname: "*.${SECRET_DOMAIN}"` → `envoy-external`) therefore reaches only the external gateway, which 404s any hostname without a matching route. This is correctly closed.

TLS: `ClientTrafficPolicy` sets `minVersion: "1.2"` on all gateways; every HTTPS listener has a `certificateRefs` to the wildcard production cert. HTTP/80 exists only to 301. **No route is served without TLS.**

47 of 55 route attachments target internal gateways.

## Internet-Exposed Surface

Reachable from the internet via the Cloudflare tunnel → `envoy-external`. All are behind the CrowdSec `SecurityPolicy` (`envoy.yaml:243-262`, `failOpen: true` — accepted design).

| Route (hostname) | Namespace | Backend | Auth? | Notes |
|---|---|---|---|---|
| `nextcloud.${DOMAIN}` | default | `nextcloud:8080` + `notify-push:7867` | App login | Highest-value target. SA token patched off; apiserver egress denied. |
| `jellyfin.${DOMAIN}` | media | `jellyfin` | App login | Writable hostPath `/var/mnt/merged/` on the pod |
| `seerr.${DOMAIN}`, `jellyseerr.${DOMAIN}` | media | `seerr:5055` | App login | Two hostnames, one backend |
| `wizarr.${DOMAIN}` | media | `wizarr` | App login (invite flow) | Invite-generation surface by design |
| `share.${DOMAIN}` | default | `picoshare` | App login | File upload/share; 100Gi ceph-block PVC |
| `homeassistant.${DOMAIN}` | network | `external-homeassistant:80` (off-cluster) | App login | Proxies to a device outside the cluster |
| `kromgo.${DOMAIN}` | observability | `kromgo:80` | **none** | Fixed badge allowlist, but publishes Talos / Kubernetes / Flux version strings publicly (V-12) |
| `flux-webhook.${DOMAIN}/hook/` | flux-system | `webhook-receiver:80` | HMAC (Flux `Receiver` token) | Path-prefix scoped to `/hook/`; correct |
| `gatus-probe.${DOMAIN}/health` | observability | `gatus:8080` | **none** | Via `envoy-external-probe`. `Exact` path + `method: GET` only — tightly scoped, acceptable |

**There is no authentication proxy in the cluster.** `grep -rni 'authelia\|oauth2-proxy\|tinyauth\|basicAuth' kubernetes` returns zero hits. Every app above depends solely on its own login page; a pre-auth RCE in any of them is directly internet-reachable.

**`type: LoadBalancer` services (2), both source-restricted:**
- `kubernetes/apps/web3/monero/p2pool/helmrelease.yaml:95-104` — `loadBalancerSourceRanges: [${LAN_CIDR}]`
- `kubernetes/apps/web3/monero/monerod/helmrelease.yaml:74-83` — `loadBalancerSourceRanges: [${LAN_CIDR}]`

Nothing unexpectedly exposed. Envoy's own LB services carry `lbipam.cilium.io/ips` on LAN addresses and are fronted by the tunnel.

## Vulnerabilities Found

| Severity | Category | Resource | Location | Description | Remediation |
|----------|----------|----------|----------|-------------|-------------|
| Critical | Workload identity / supply chain | `cluster-runner` scale set | `kubernetes/apps/actions-runner-system/actions-runner-controller/runners/cluster/helmrelease.yaml:50-52,68-71` + `rbac.yaml:14-19` + `.github/workflows/agent-pr-review.yaml:20` | Every runner pod mounts the Talos `os:admin` credential at `/var/run/secrets/talos.dev`, and `ACTIONS_RUNNER_REQUIRE_JOB_CONTAINER: "false"` runs job steps directly in that pod. `agent-pr-review` runs the third-party `misospace/pr-reviewer-action` against `github.event.pull_request.head.sha` on that same pod — but it needs no Talos access at all; only `flate`'s image-pull job does. `os:admin` on a public repo's self-hosted runner is node root plus the full machine config (cluster CA, etcd keys). Mitigating: both jobs gate on `head.repo.full_name == github.repository`, so forks cannot reach the runner. | Split into two scale sets: a plain `cluster-runner` with no Talos volume for `agent-pr-review`, and a `cluster-runner-talos` mounting the secret used only by `flate`'s `pull` job. |
| High | Pod security | apiserver admission config | `talos/patches/controller/cluster.yaml:3-5` + `kubernetes/components/common/namespace.yaml:9-10` | `admissionControl: {$$patch: delete}` removes Talos's default `enforce: baseline` PodSecurity config, and the namespace component sets only `warn`/`audit`. No namespace in the cluster enforces any PSA level. | Add `pod-security.kubernetes.io/enforce: baseline` to `components/common/namespace.yaml` and exempt the two `kube-system` privileged DaemonSets with a namespace-level override. |
| High | Supply chain | cluster-wide | (absence) | No Kyverno / Gatekeeper / `ValidatingAdmissionPolicy` anywhere. Nothing rejects a privileged pod, a hostPath mount, a missing seccomp profile, or an unexpected `envoy-external` attachment at admission time. | Ship one `ValidatingAdmissionPolicy` (built-in, no operator) covering: deny `privileged`/`hostPath` outside `kube-system`, and deny `parentRefs.name: envoy-external` outside an allowlisted namespace set. |
| High | Network policy | 12 namespaces | `kubernetes/apps/kube-system/network-policies/app/` | Only 4 CNPs exist, covering `database` (ingress) and `media`/`default`/`web3` (apiserver-egress deny). `actions-runner-system`, `network`, `flux-system`, `ai`, `rook-ceph`, `security`, `observability`, `cert-manager`, `kube-system`, `kopiur-system`, `openebs-system`, `system-upgrade` have none. An internet-exposed app in `default`/`media` has unrestricted east-west reach to every one of them, including direct pod-to-pod access to `envoy-external` (bypassing the CrowdSec gateway policy). | Extend the existing `database-ingress-allowlist` pattern to `network`, `flux-system`, `actions-runner-system`, and `ai` first — those four hold the tunnel token, the Git token, the Talos secret, and the LLM provider keys respectively. |
| High | Ingress exposure | `envoy-external` | `kubernetes/apps/network/envoy-gateway/app/envoy.yaml:77-80` | `listeners[https].allowedRoutes.namespaces.from: All`. Any namespace can publish itself to the internet by adding one `parentRef`, and external-dns will mint the public DNS record automatically. With no admission policy (above), nothing catches this in review or at apply time. | Change to `from: Selector` with a `gateway.home.arpa/external: allow` namespace label, applied to the 5 namespaces that legitimately need it. |
| Medium | Ingress exposure | 8 external routes | see Internet-Exposed Surface table | No authentication layer exists in the cluster (no authelia / oauth2-proxy / tinyauth). Every internet-reachable app relies solely on its own login page; a pre-auth vulnerability in nextcloud, jellyfin, seerr, wizarr, or picoshare is directly exploitable from the internet. | Put an ext-auth `SecurityPolicy` in front of the routes that have no legitimate anonymous/API use — `picoshare`, `wizarr`, `kromgo` are the cheapest wins. |
| Medium | Supply chain | 152 `OCIRepository` | repo-wide | `spec.verify` is set on zero of them. Flux pulls every chart with no cosign signature check. | Add `spec.verify.provider: cosign` with the keyless issuer to the `home-operations` charts, which are signed. |
| Medium | Pod privilege | `amdgpu-undervolt` DaemonSet | `kubernetes/apps/kube-system/amdgpu-undervolt.yaml:150-168` | `privileged: true` + `allowPrivilegeEscalation: true` + writable hostPath `/sys` on every GPU node, running an inline shell script that writes `power1_cap`. Functionally justified, but `privileged` already implies escalation and `/sys` write is broader than the sysfs paths it touches. | Drop the redundant `allowPrivilegeEscalation: true`; consider a `subPath`-scoped `/sys/class/drm` mount instead of all of `/sys`. |
| Medium | Supply chain | 9 container images | see §5 table | Tag-only, no digest. `steinbrueckri/envsubst:v2.1.7` (unaudited personal Docker Hub account) and `postgres:18` (floating major) are the two that matter. The ik_llama incident already showed tags in this cluster get rebuilt in place. | Pin all 9 to `tag@sha256:…`; Renovate already handles digest bumps for the rest of the repo. |
| Medium | Exposure | Talos control-plane components | `talos/patches/controller/cluster.yaml:9-10,14-15,25` | `controllerManager.bind-address: 0.0.0.0`, `scheduler.bind-address: 0.0.0.0`, `etcd.listen-metrics-urls: http://0.0.0.0:2381` bind unauthenticated metrics on every node interface, reachable from the whole LAN. | Standard home-ops template default; if the LAN is not trusted, bind to the node IP and restrict via a `CiliumClusterwideNetworkPolicy` on host endpoints. |
| Low | Workload identity | `opencode` | `kubernetes/apps/ai/opencode/app/helmrelease.yaml:62` | `automountServiceAccountToken: true` with no `serviceAccount:` or `rbac:` block anywhere in the app dir — mounts an unusable `default`-SA token into an AI coding agent for no reason. | Set to `false`. |
| Low | Information disclosure | `kromgo` | `kubernetes/apps/observability/kromgo/app/helmrelease.yaml:16-32,94-99` | Publishes Talos, Kubernetes, and Flux version strings unauthenticated to the internet — free CVE targeting for anyone who finds the host. Queries are a fixed allowlist, so no arbitrary PromQL. | Move `kromgo` to `envoy-internal`, or drop the three `*_version` badges. |
| Low | Pod security | 89 HelmReleases | repo-wide | `seccompProfile: RuntimeDefault` is declared in only 20 of 109 HelmReleases. Some inherit it from chart defaults but nothing guarantees it. | Covered by the PSA `enforce: baseline` action above (baseline requires `RuntimeDefault` or `Localhost`). |
| Low | Pod security | `klipper-exporter` | `kubernetes/apps/observability/exporters/klipper-exporter/app/helmrelease.yaml:43` | `runAsNonRoot: false` at pod level. Container-level controls (`allowPrivilegeEscalation: false`, `drop: [ALL]`, `readOnlyRootFilesystem: true`) contain it. | Test `runAsNonRoot: true` + an explicit uid; revert if the image needs uid 0. |

## Action Items

- [ ] **(Critical)** Split the runner scale set: `cluster-runner` without the Talos volume for `agent-pr-review`, `cluster-runner-talos` with it for `flate`'s `pull` job only.
- [ ] **(High)** Add `pod-security.kubernetes.io/enforce: baseline` to `kubernetes/components/common/namespace.yaml`, with an exemption path for `kube-system`'s two privileged DaemonSets.
- [ ] **(High)** Ship one `ValidatingAdmissionPolicy` (no operator needed) denying `privileged`/`hostPath` outside `kube-system` and denying `envoy-external` parentRefs outside an allowlist.
- [ ] **(High)** Extend the `database-ingress-allowlist` CNP pattern to `network`, `flux-system`, `actions-runner-system`, and `ai`.
- [ ] **(High)** Change `envoy-external` HTTPS listener to `allowedRoutes.namespaces.from: Selector` with a `gateway.home.arpa/external: allow` label.
- [ ] **(Medium)** Add an ext-auth `SecurityPolicy` in front of `picoshare`, `wizarr`, and `kromgo`.
- [ ] **(Medium)** Add `spec.verify.provider: cosign` to the `home-operations` `OCIRepository` objects.
- [ ] **(Medium)** Digest-pin the 9 tag-only container images; start with `steinbrueckri/envsubst` and `postgres:18`.
- [ ] **(Medium)** Drop the redundant `allowPrivilegeEscalation: true` from `amdgpu-undervolt`.
- [ ] **(Low)** Set `automountServiceAccountToken: false` on `opencode`.
- [ ] **(Low)** Move `kromgo` to `envoy-internal` or drop its version badges.

## Summary Stats

- Total issues: 14
- Critical: 1 | High: 4 | Medium: 5 | Low: 4

**Verified clean (no action):** no `cluster-admin` or wildcard RBAC anywhere; no `system:masters`/`system:authenticated` bindings; no plaintext committed secret; `age.key*`, `kubeconfig`, `talosconfig`, and `talos/clusterconfig/` all gitignored and untracked (confirmed via `git ls-files`); `.sops.yaml` covers every path holding encrypted files and fails closed elsewhere; no container/CRI socket hostPath; no added Linux capabilities; no `hostPID`/`hostIPC`; the existing CNPs do **not** carry the kube-system/CoreDNS rev-NAT bug; both `LoadBalancer` services are source-restricted to the LAN; no route is served without TLS; internal gateways are not publicly resolvable; `homepage` and `litellm` RBAC are exemplary least-privilege; `gitleaks` runs on every push and PR with sha-pinned actions.
