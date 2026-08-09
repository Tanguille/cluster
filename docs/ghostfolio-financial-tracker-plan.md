# Ghostfolio Self-Hosted Financial Tracker + MCP Plan

Branch: `feat/ghostfolio-mcp`
Worktree: `.claude/worktrees/ghostfolio-mcp`

Background: full research and option comparison lives in
[`docs/financial-tracking-research.md`](./financial-tracking-research.md). Decision: self-host
**Ghostfolio** (stocks, AGPL-3.0, active) for the tracker, and adopt the existing
[`mhajder/ghostfolio-mcp`](https://github.com/mhajder/ghostfolio-mcp) MCP server (Python,
published to `ghcr.io/mhajder/ghostfolio-mcp`) rather than writing a custom bridge. rotki
(crypto/DeFi/Trezor) is deferred to a follow-up.

This repo has no official Ghostfolio Helm chart and no vendored third-party chart repo entry for
one either, so the app is built with `app-template` (bjw-s) wired to the cluster's shared CNPG
Postgres and Dragonfly Redis — the same pattern as `nextcloud`/`karakeep`, not the community
`ghostfolio-helm` chart (which bundles its own Bitnami Postgres/Redis subcharts and would
duplicate the shared-cluster convention already in place).

## Step 1 — CNPG database + role — DONE

- `kubernetes/apps/database/cloudnative-pg/cluster/cluster.yaml`: add `ghostfolio` to
  `spec.managed.roles` (`login: true`, `passwordSecret.name: ghostfolio-db`).
- `kubernetes/apps/database/cloudnative-pg/cluster/roles/ghostfolio.sops.yaml`: new SOPS-encrypted
  `kubernetes.io/basic-auth` Secret, namespace `database`, label `cnpg.io/reload: "true"`,
  generated username/password — mirrors `roles/nextcloud.sops.yaml`.
- `kubernetes/apps/database/cloudnative-pg/cluster/kustomization.yaml`: add
  `roles/ghostfolio.sops.yaml`.
- `kubernetes/apps/database/cloudnative-pg/databases/resourceset.yaml`: add `ghostfolio` to
  `spec.inputs` (creates the `Database` CR, `owner: ghostfolio`).

## Step 2 — Ghostfolio HelmRelease — DONE

- `kubernetes/apps/default/ghostfolio/ks.yaml`, `app/kustomization.yaml`,
  `app/helmrelease.yaml`, `app/secret.sops.yaml` — mirror `kubernetes/apps/default/karakeep`.
- Image: `docker.io/ghostfolio/ghostfolio:3.45.0` pinned to digest (resolve digest at
  implementation time — Renovate tracks it afterward like every other pinned image in this repo).
- Env (from Ghostfolio's own `.env.example`):
  - `REDIS_HOST=dragonfly.database.svc.cluster.local`, `REDIS_PORT=6379` (no Dragonfly auth
    configured cluster-wide, so no `REDIS_PASSWORD`).
  - `DATABASE_URL` — full `postgresql://ghostfolio:<password>@pgbouncer-rw.database.svc.cluster.local:5432/ghostfolio?connect_timeout=300`
    connection string, password baked in directly, stored whole in `ghostfolio-secret`
    (`secretKeyRef`) — Ghostfolio reads `DATABASE_URL` as one opaque string, not
    template pieces, so there's no separate password env to interpolate.
  - `DIRECT_URL` — same connection string but pointed at `postgres16-rw` (the CNPG cluster's own
    service), bypassing `pgbouncer-rw`. Required because the pooler runs in `transaction` mode and
    Prisma migrations need session-level continuity (advisory locks) that mode doesn't provide.
  - `ACCESS_TOKEN_SALT`, `JWT_SECRET_KEY` — random strings, generated at implementation time and
    stored in `app/secret.sops.yaml`.
- `route`: internal HTTPRoute via `envoy-internal`, homepage annotations — mirror karakeep.
- No persistent volume needed beyond Postgres/Redis (Ghostfolio is stateless aside from the DB).

## Step 3 — Register with toolhive/vmcp — DONE

- `kubernetes/apps/ai/toolhive/config/ghostfolio.yaml`: new `MCPServer` (`toolhive.stacklok.dev/v1beta1`),
  `image: ghcr.io/mhajder/ghostfolio-mcp:1.5.0@sha256:4436268d0a1604e5bc1c9080f0005e7b5e0b80163803f95519b7a4537cd6660c`,
  `transport: stdio`, `groupRef: {name: all}`, env `GHOSTFOLIO_URL: http://ghostfolio.default.svc.cluster.local:3333`,
  `READ_ONLY_MODE: "true"` initially (matches the cluster's cautious-by-default posture; can be
  turned off later once the agent's recommendations are trusted), secret `GHOSTFOLIO_TOKEN` from
  `toolhive-secrets` — mirror `karakeep.yaml`.
- `kubernetes/apps/ai/toolhive/app/secret.sops.yaml`: add `GHOSTFOLIO_TOKEN` key (a Ghostfolio API
  token generated once the app is up — manual step, needs Tanguille to log in and mint it).
- `kubernetes/apps/ai/toolhive/config/kustomization.yaml`: add `ghostfolio.yaml`.
- Optional `MCPToolConfig` to rename/filter tools once we see the actual tool list `ghostfolio-mcp`
  exposes (it has many — portfolio, holdings, transactions, accounts, symbol lookup; may want to
  trim to read-oriented tools given `READ_ONLY_MODE` is on anyway).

## Step 4 — Wire into Flux — DONE

- `kubernetes/apps/default/kustomization.yaml`: add `./ghostfolio/ks.yaml`.

## Step 5 — Draft PR — DONE

- Verified all four touched Kustomizations (`ghostfolio/app`, `toolhive/config`,
  `cloudnative-pg/cluster`, `cloudnative-pg/databases`) render cleanly via `kustomize build`.
- `/simplify`'s 4-agent review found two real issues, both fixed: (1) probes' repeated `httpGet`
  block collapsed to a YAML anchor (matches `searxng`/`homepage`), (2) `ks.yaml` was missing the
  `dependsOn: [cloudnative-pg-cluster, dragonfly-cluster]` block every sibling app on the shared
  DB/Redis declares (now added, with `wait: true` to match `nextcloud`).
- Opened as a **draft** PR (explicit user request, despite this being the primary repo rather than
  an upstream one).
- Rebased onto `main` after it moved (a `postgres-mcp` toolhive server and a crowdsec rule change
  landed concurrently) — resolved conflicts in the shared CNPG role list, its kustomization, and
  `toolhive-secrets` (re-added `GHOSTFOLIO_TOKEN` via `sops --set` onto main's version rather than
  text-merging ciphertext).
- CodeRabbit's review found real issues, fixed: (1) `ks.yaml` was missing `dependsOn` on
  `cloudnative-pg-databases` — the `ghostfolio` Database CR is applied by that Kustomization, not
  `cloudnative-pg-cluster`; (2) missing `DIRECT_URL` — `pgbouncer-rw` runs in `transaction` mode,
  which doesn't support the session-level continuity Prisma migrations need, so migrations need a
  direct path to `postgres16-rw`; (3) doc accuracy: CoinGecko MCP attribution, an unsupported ToS
  enforcement-risk claim, and stale recommendations superseded by the actual deployed shape — all
  corrected in `docs/financial-tracking-research.md`. One finding (add `ROOT_URL`) was verified
  against Ghostfolio's own source and `.env.example` and found to not exist as a real config
  variable — skipped as a false positive.

**Manual follow-up after Flux reconciles** (not scriptable from the PR):
1. Log into Ghostfolio's first-run setup and create the account.
2. Mint an API token in Ghostfolio and replace the `GHOSTFOLIO_TOKEN` placeholder
   (`REPLACE_ME_AFTER_GHOSTFOLIO_FIRST_RUN`) in `kubernetes/apps/ai/toolhive/app/secret.sops.yaml`
   with the real token via `sops --set`.
3. Confirm the `ghostfolio` MCPServer comes up healthy in toolhive and the agent can query it.

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been
  consolidated into existing documentation, the plan file can be removed. If there is no relevant
  existing documentation, the plan should be reworked into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.
