# SOPS Age Post-Quantum Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended for this plan) to implement task-by-task with a checkpoint after every task. Do NOT use subagent-driven-development for this plan — every task after Task 3 touches live in-cluster secret decryption, and each push needs an explicit human go/no-go before the next task starts. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate this repo's Flux-managed SOPS secrets from classical X25519 age encryption to post-quantum-safe hybrid age encryption (X25519+ML-KEM-768), via a zero-downtime dual-recipient rollover, without ever making secrets undecryptable in-cluster.

**Architecture:** Generate a new `age -pq` hybrid keypair. Add its recipient alongside the existing classical recipient in `.sops.yaml` (age accepts a comma-separated recipient list — SOPS wraps the data key to *every* listed recipient, so *either* identity alone can decrypt). Re-encrypt all in-scope secrets to both recipients. Push the highest-blast-radius file first (`sops-age.sops.yaml`, which holds the private key Flux itself uses to decrypt everything else) in isolation and verify Flux still reconciles before touching anything else. Only after a burn-in period with dual-recipient encryption proven stable, drop the classical recipient and re-encrypt to PQ-only. See `docs/sops-post-quantum.md` for the prior sandbox test that validated this toolchain and first sketched this approach — this plan is the concrete, repo-specific execution of it.

**Tech Stack:** `age` v1.3.1 (pinned `.mise.toml`), `sops` 3.13.2 (pinned `.mise.toml`), FluxCD 2.9.0 / `kustomize-controller` v1.9.1 (confirmed already running in-cluster — no Flux upgrade needed).

## Global Constraints

- Never encrypt to the PQ recipient alone until Task 6's burn-in has passed — classical + PQ dual-recipient at every step until explicitly cutting over in Task 7.
- `talos/talsecret.sops.yaml` is **out of scope for re-encryption** in this plan. It's decrypted by `talhelper`/`talosctl`, not Flux's `kustomize-controller`, and `talhelper`'s SOPS/age dependency compatibility with PQ recipients is an open, answerable question — Task 8 resolves it and records the outcome, so this isn't a silent gap.
- Every `git push` in this plan requires explicit user confirmation at the moment it happens — a prior "yes, do the migration" does not authorize any specific push (per repo convention: prepare locally, show the diff, ask).
- All generated/backup key material must use a filename ending in `.key` so `.gitignore`'s `*.key` rule covers it — never let raw key material land in a git-tracked file, and never pass key material as a shell argument (visible in process listings) — use `--value-file`/file redirection instead.
- Run every command from the worktree root unless a step says otherwise.
- CI was checked (`grep -rIln "SOPS_AGE_KEY\|sops" .github/workflows`) and no workflow references a SOPS/age credential — no CI task is needed in this plan.

---

## Process Instructions

- After completing each step, update this plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of this plan have been consolidated into existing documentation, the plan file can be removed. `docs/sops-post-quantum.md` already exists and is the correct target — it currently documents an isolated sandbox test of PQ SOPS/age (no repo changes); Task 9 turns it into the record of the actual completed migration.

**Important**: Every prompt should verify the branch and worktree before doing any work.

## Branch & Worktree

- **Branch:** `feat/sops-age-post-quantum`
- **Worktree:** `.worktrees/sops-age-post-quantum`
- Setup command (Task 1): `git worktree add .worktrees/sops-age-post-quantum -b feat/sops-age-post-quantum` — see `.agents/skills/git-worktree-isolation/SKILL.md` for this repo's general worktree create/validate/cleanup pattern; the copy-list below is a CLAUDE.md-specific addition on top of that skill's default.
- Files to copy into the worktree (per repo convention — `.mcp.json`, `.env`, `CLAUDE.local.md` do not exist in this repo, skip them):
  - `.vscode/`
  - `.claude/`
  - `age.key` (required locally for `SOPS_AGE_KEY_FILE` — not committed, gitignored via `*.key`)

---

### Task 1: Preflight verification + worktree setup — ✅ complete (2026-07-11)

Status: main clean/in sync with origin. In-cluster `kustomize-controller` is `v1.9.2` (above the v1.9.0 floor). Worktree + branch already existed from the PR #3622 prep session; reused rather than recreated. `age.key` copied in, classical decryption verified working from the worktree.

**Files:** none modified — verification only.

- [x] **Step 1: Verify branch and repo state**

```bash
git pull && git status
git branch --show-current   # expect: main
```

- [x] **Step 2: Verify the actual in-cluster kustomize-controller version (not just the CLI pin — the cluster doesn't auto-upgrade when `.mise.toml` changes)**

```bash
kubectl -n flux-system get deployment kustomize-controller \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

Expected: `ghcr.io/fluxcd/kustomize-controller:v1.9.1@sha256:...` or newer. This was already confirmed live on 2026-07-02 — re-confirm at execution time in case the cluster has since rolled back or someone downgraded Flux. If the version is below v1.9.0, **stop** — PQ recipients will not decrypt in-cluster and the rest of this plan is unsafe to run.

- [x] **Step 3: Create the worktree**

```bash
git worktree add .worktrees/sops-age-post-quantum -b feat/sops-age-post-quantum
cp -r .vscode .claude age.key .worktrees/sops-age-post-quantum/
cd .worktrees/sops-age-post-quantum
```

- [x] **Step 4: Verify tooling resolves inside the worktree and existing decryption still works (supersedes a plain version check — this exercises the real path)**

```bash
sops --version   # expect sops 3.13.2+
age --version    # expect v1.3.1+
SOPS_AGE_KEY_FILE=./age.key sops -d kubernetes/components/common/cluster-secrets.sops.yaml > /dev/null && echo "OK: existing classical decryption still works in worktree"
```

- [x] **Step 5: Update plan status to "Task 1 complete", pause for confirmation before Task 2.**

---

### Task 2: Generate the PQ keypair, back up the classical key — ✅ complete (2026-07-11)

Status: `age-classical-backup.key` and `age-pq.key` created (gitignored, worktree-local). `age.key` rebuilt as dual-identity file; `age-keygen -y age.key` confirmed both recipients present (classical `age12gul5m0...` + PQ `age1pq1f696t70thmae32r7s6eqscyncz4tveaqsd53k2pw82kems2v02mcmeuj4j85swr6zdp3z3ay6kuq26z9d37928w58se3a7cvghzzlthmhfmdcn66sc8kcwp56672yjn5szvqet5q5v46a3kphepytfstdah95v95awn25jvc8ce2nmes0cqykax6n2tkf94pxqcjkn55wjjrgmk2dgh2xggl5gskxy4t20x88pqqtgnfe8pyymfuwmy3e9muvja9x3gf8can07seky8hv6ve9v0auzdyhpa2xwczme5ejxtdx6xtqp47cwe8q026gzh5wz0x82dezz5jkgznskq960ltwtfxsdj27seql0rtu3jqqkkm4xgqcfzjnxrzyevea8vgz5hr8053yt56qd9r6umydx93usfuzznen9wlew6500ydcqpjk2jm4x0ewy34ngjpj4uwu4p5dzynca4grrfghsnllum865u9f8py3nq69r0kjarppxfgvfgptuqpxkar2wvq7r8ys4vuuc2pj0433ceax49znp8sfqj5dl29ulcczqqw20pcjygxkc26tzv35ln8c7jjfre957v88wq9n8xxf4qgw3wpv2hh74gwuacnr2f4krkxzjs45a46v62jdkgmq0qnk3vhe9s2kgfjw3dzn6d4fnwyrt4ke6fverqc4x4mxpj6ffahw3mqv8g6vjgg2p46u53zrhgsnyr4x5dmxsmmpzdhjdu436ey89qmxgca8sjdwuzjhcvtewm9yppzjs9ef2p8q2mdjfcjh3uu3fq5py04sv8pskjvqd9y0ta8axmh239u4zlrgpdmwfe7r0wpgt788xgjwjx0vxdjr5gzk4quukyv2rvefjacg38mpwqyjaxv9q294v4f03g5x3ve79c27fgt3u34vmrcty09sje3yw2rraa5x9y5t3u4rtcpu5ay8jrx44u8dfehw5ulgfgqjza0lce4tz6s34uq00ayrg6vk6hgjuql55z6taltx04mjehmkk487ddd5zuzguqu8nvz2d5fhgl6sx3x775zdw29xnr8jgmltyfze2ymsz92u2dk4v70jzfsvc34m3ajkg9965rcqe9nn3aehwcr047p6xfnc55cv6tjewdrg6mff4glrwd9p8q5sa9repr0w3hzw42d9dys6u8hr50v83v8y5lzncql6ty9gfkcc4pcst683ddz2vky7u9g8gkrjy440r8yrsdw5cq6cpj23fjqxrj0t2gj5cpyv6407luycyd8p6pmjym3g46w2aj7f79nce73g30zwr4hx7mz2emrwkkgst53q85zyveg7fen5vtfjc3y6m0f5mkwj3yq5643c3jstgcz9fgsgu5hdx4u27wud5a3kgv8ysdh9z295mxh6j2pp96qwk34jevrqhn8zutwvvzwla53mrqh6czjpq6ud0c0psd7zznkgjczqsppq36ujygqnz4nc5sg40ngkfh3k5qyfdvx4slv9xcaceu83mzzr0unc3y4fznt0dal42z2kye8vxjz4yur33azljufrzg3d0lmxw2n53efhjr8wxtfstz4jjhn9pynzf425jryfpgf23pc6ykm4049qgu2xsz2nyrhty5yjla9xueyxwka77ztx7n6ndak0lg5n2fpveff8j2g49dyp3wqsvqmnm638nxwrd3g4hxqese8mmwt3cmr0jdykdnvqsmd9zumt0e6jkaktpppspsw4szfu7ft79r8gqwspdw7256lqmpr6r6hm2xxv0uwzqw6q3ffcy4y9n7tzxzhr22gf3qwxu8rhq9ceuj8jd4qsc7yexsa27d3dkwelklhk77c8zlf5aeg8y3usluk4y749ltlt7eh5xyf8al09nwljekxe74s7uj0hd07sm0zxgqal48prwgvl9svx0ucvze2yy3yygsqjwpc`).

**Files:**
- Create: `age-classical-backup.key` (worktree root, gitignored)
- Create: `age-pq.key` (worktree root, gitignored)
- Modify: `age.key` (worktree root, gitignored) — becomes a dual-identity file

**Interfaces:**
- Produces: `PQ_RECIPIENT` env var (the `age1pq1...` public recipient string) — Task 3 consumes this.

- [x] **Step 1: Back up the current classical-only identity file before mutating it**

```bash
cp age.key age-classical-backup.key
```

- [x] **Step 2: Generate the hybrid PQ keypair and extract its recipient**

Same extraction idiom already validated in `docs/sops-post-quantum.md`'s sandbox test:

```bash
age-keygen -pq -o age-pq.key
PQ_RECIPIENT=$(grep '^# public key:' age-pq.key | cut -d: -f2 | tr -d ' ')
```

- [x] **Step 3: Build the dual-identity file SOPS_AGE_KEY_FILE points to**

```bash
cat age-classical-backup.key age-pq.key > age.key
```

- [x] **Step 4: Verify the combined identity file derives both recipients**

```bash
age-keygen -y age.key
```

Expected: two lines — the existing classical recipient (`age12gul5m0dg9nn2gk69uvzwdxyluh5xt02m8wvyg9hn7c93nz7xehswmdenq`) and the new `age1pq1...` recipient (this is also the authoritative check that `PQ_RECIPIENT` from Step 2 was extracted correctly — no separate sanity check needed).

- [x] **Step 5: Update plan status to "Task 2 complete" (record `PQ_RECIPIENT` value in the status notes), pause for confirmation before Task 3.**

---

### Task 3: Add the PQ recipient to `.sops.yaml` (dual-recipient, kubernetes/bootstrap rule only)

**Files:**
- Modify: `.sops.yaml`

**Interfaces:**
- Consumes: `PQ_RECIPIENT` from Task 2.

- [x] **Step 1: Edit the second creation rule only — leave the `talos/` rule untouched (out of scope)**

Current content:
```yaml
---
creation_rules:
  - path_regex: talos/.*\.sops\.ya?ml
    mac_only_encrypted: true
    age: "age12gul5m0dg9nn2gk69uvzwdxyluh5xt02m8wvyg9hn7c93nz7xehswmdenq"
  - path_regex: (bootstrap|kubernetes)/.*\.sops\.ya?ml
    encrypted_regex: "^(data|stringData)$"
    mac_only_encrypted: true
    age: "age12gul5m0dg9nn2gk69uvzwdxyluh5xt02m8wvyg9hn7c93nz7xehswmdenq"
stores:
  yaml:
    indent: 2
```

Change only the `age:` value under the `(bootstrap|kubernetes)/...` rule to a comma-separated list of the classical recipient and `$PQ_RECIPIENT`:

```yaml
  - path_regex: (bootstrap|kubernetes)/.*\.sops\.ya?ml
    encrypted_regex: "^(data|stringData)$"
    mac_only_encrypted: true
    age: "age12gul5m0dg9nn2gk69uvzwdxyluh5xt02m8wvyg9hn7c93nz7xehswmdenq,${PQ_RECIPIENT}"
```

(Use the Edit tool with the exact old/new block above — do not use a blind `sed` replace, the classical recipient string appears twice in the file and an unanchored replace would also add the PQ recipient to the out-of-scope `talos/` rule.)

- [x] **Step 2: Verify the YAML is valid and only the second rule changed**

```bash
yq e '.creation_rules' .sops.yaml
git diff .sops.yaml
```

Confirm: rule 0 (`talos/...`) unchanged, rule 1 (`(bootstrap|kubernetes)/...`) now has both recipients comma-separated.

- [x] **Step 3: Commit (no push yet)**

```bash
git add .sops.yaml
git commit -m "feat(sops): add post-quantum age recipient alongside classical key"
```

- [x] **Step 4: Update plan status to "Task 3 complete", pause for confirmation before Task 4.**

---

### Task 4: Re-encrypt `sops-age.sops.yaml` first, in isolation — this is the highest blast-radius file — ✅ complete (2026-07-11), verified live in-cluster

This file holds the private key Flux's `kustomize-controller` uses to decrypt *every other* secret in the cluster. It must be updated and verified alone, before any other secret is touched — and unlike a routine secret, its plaintext must never touch disk during the edit.

**Correction found during execution:** `sops set --value-file` failed with `Value for --set is not valid JSON` — `sops set`'s value (whether inline, `--value-file`, or `--value-stdin`) must always be JSON-encoded, even multi-line file content. Fixed with `jq -Rs . age.key | sops set --value-stdin ...` (JSON-encodes the content, still never touches the command line or an intermediate plaintext file). Separately, `sops set` only rewrites the target field — it does **not** rewrap the file's outer recipients to match `.sops.yaml`, so the "expect: 2 recipients" check failed until a follow-up `sops updatekeys -y` was run. Both are now required in Step 1.

**Files:**
- Modify: `kubernetes/components/common/sops-age.sops.yaml`

- [x] **Step 1: Update the `age.agekey` field in place, then rewrap outer recipients to match `.sops.yaml`**

```bash
jq -Rs . age.key | sops set --value-stdin kubernetes/components/common/sops-age.sops.yaml \
  '["stringData"]["age.agekey"]'
sops updatekeys -y kubernetes/components/common/sops-age.sops.yaml
grep -c "recipient:" kubernetes/components/common/sops-age.sops.yaml   # expect: 2
```

- [x] **Step 2: Verify BOTH the current in-cluster identity (classical-only) and the new PQ-only identity can independently decrypt this file — this is the safety gate before pushing**

```bash
SOPS_AGE_KEY_FILE=age-classical-backup.key sops -d kubernetes/components/common/sops-age.sops.yaml > /dev/null \
  && echo "OK: classical-only decrypt still works (Flux won't break on push)"

SOPS_AGE_KEY_FILE=age-pq.key sops -d kubernetes/components/common/sops-age.sops.yaml > /dev/null \
  && echo "OK: PQ-only decrypt works"
```

Both must print OK. If either fails, do not proceed — fix before committing.

- [x] **Step 3: Commit and push**

```bash
git add kubernetes/components/common/sops-age.sops.yaml
git commit -m "feat(sops): re-encrypt sops-age secret with post-quantum recipient"
git push -u origin feat/sops-age-post-quantum
```

Open/update a PR if that's this repo's normal flow for cluster changes, or note that Flux tracks `main` and this needs merging before it reconciles — confirm with the user which applies here before pushing.

**What actually happened:** Flux tracks `main`, and this change was sitting on the still-open PR #3622 branch — a push to the branch alone does nothing until merged. Before merging, got two independent second opinions (Fable 5 and GPT via Codex) on whether the dual-recipient rollover of the master decryption secret was safe; both returned GO, citing actual `kustomize-controller` v1.9.2 source (fresh secretRef `Get` + `ImportKeys` per reconcile, no restart path) and SOPS/age multi-recipient design (independent per-recipient envelopes). Also ran an extra smoke test: the exact dual-identity `age.key` content written into the secret was used to decrypt an unrelated real secret elsewhere in the repo, confirming the concatenated format is well-formed. PR #3622 squash-merged to `main` (`fba3267b0`).

- [x] **Step 4: Watch Flux reconcile and confirm the in-cluster secret actually updated**

```bash
flux get kustomizations -A
kubectl -n flux-system get secret sops-age -o jsonpath='{.data.age\.agekey}' | base64 -d
```

Expected: the decoded secret now contains *both* the classical `AGE-SECRET-KEY-1...` block and the PQ `AGE-SECRET-KEY-PQ-1...` block. If it doesn't, check `kubectl -n flux-system logs deploy/kustomize-controller --since=10m` for decrypt errors before restarting `kustomize-controller`.

**What actually happened:** Forced `flux reconcile kustomization flux-system --with-source`. Confirmed via count-only greps (no key content printed) that the live `sops-age` Secret contains exactly one classical and one PQ identity block. **No controller restart needed** — matches both models' prediction. All Kustomizations cluster-wide reported `Ready=True` at revision `fba3267b0` within seconds, zero decrypt errors in `kustomize-controller` logs.

- [x] **Step 5: Update plan status to "Task 4 complete", record what was actually observed (secret updated cleanly / needed a controller restart / etc). Pause for confirmation before Task 5.**

---

### Task 5: Re-encrypt the remaining in-scope secrets

**Files:** all other `*.sops.yaml` under `kubernetes/` and `bootstrap/` (≈42 files — full list can be regenerated with the find command below; excludes `kubernetes/components/common/sops-age.sops.yaml` already done in Task 4, and excludes `talos/talsecret.sops.yaml`, out of scope).

- [ ] **Step 1: Re-encrypt all in-scope files to both recipients, in parallel — each file's rewrap is independent, no shared mutable state**

```bash
find kubernetes bootstrap -name '*.sops.yaml' \
  ! -path 'kubernetes/components/common/sops-age.sops.yaml' -print0 \
  | xargs -0 -P4 -I{} sops updatekeys -y {}
```

- [ ] **Step 2: Verify every file now lists 2 recipients**

```bash
for f in $(find kubernetes bootstrap -name '*.sops.yaml' \
  ! -path 'kubernetes/components/common/sops-age.sops.yaml'); do
  n=$(grep -c "recipient:" "$f")
  echo "$f: $n recipients"
done | grep -v ": 2 recipients"
```

Expected: no output (every file shows exactly 2). `sops-age.sops.yaml` is excluded here too since Task 4 Step 1 already confirmed its recipient count.

- [ ] **Step 3: Commit and push**

```bash
git add kubernetes bootstrap
git commit -m "feat(sops): re-encrypt all secrets with post-quantum age recipient"
git push
```

- [ ] **Step 4: Watch Flux reconcile all Kustomizations cleanly**

```bash
flux get kustomizations -A
kubectl -n flux-system logs deploy/kustomize-controller --since=15m | grep -i "error\|decrypt" || echo "no decrypt errors"
```

Expected: all Kustomizations `Ready=True`, no decrypt errors in controller logs.

- [ ] **Step 5: Update plan status to "Task 5 complete", pause for confirmation before Task 6.**

---

### Task 6: Burn-in verification

No file changes — this is a soak/observation task before the irreversible cutover in Task 7.

- [ ] **Step 1: Confirm no new pod instability correlated with the re-encryption push**

```bash
kubectl get pods -A | grep -v -E "Running|Completed"
```

- [ ] **Step 2: Force a full reconcile to double-check drift-free state, using the repo's existing reconcile target**

```bash
task reconcile
flux get kustomizations -A
```

(`task reconcile` wraps `flux reconcile source git flux-system` + `flux reconcile kustomization cluster-apps -n flux-system --with-source` + `flux reconcile kustomization flux-system --with-source` — the actual Kustomization names in this repo, defined in `Taskfile.yaml`.)

- [ ] **Step 3: Soak.** Recommend at least one full day before Task 7's cutover, so any secret that's only read at a slow interval (cronjobs, periodic reloaders) gets a chance to prove it decrypts correctly. This duration is a judgment call — confirm with the user before proceeding.

- [ ] **Step 4: Update plan status to "Task 6 complete — burn-in passed as of <date>", pause for confirmation before Task 7.**

---

### Task 7: Cut over — drop the classical recipient

**Files:**
- Modify: `.sops.yaml`
- Modify: all in-scope `*.sops.yaml` files (re-wrap only, ciphertext payload unchanged)

- [ ] **Step 1: Edit `.sops.yaml` — remove the classical recipient from the kubernetes/bootstrap rule, leaving only the PQ recipient**

```yaml
  - path_regex: (bootstrap|kubernetes)/.*\.sops\.ya?ml
    encrypted_regex: "^(data|stringData)$"
    mac_only_encrypted: true
    age: "${PQ_RECIPIENT}"
```

- [ ] **Step 2: Re-wrap all in-scope secrets to PQ-only, in parallel — this includes `sops-age.sops.yaml`, whose outer SOPS encryption goes PQ-only like every other file**

```bash
find kubernetes bootstrap -name '*.sops.yaml' -print0 \
  | xargs -0 -P4 -I{} sops updatekeys -y {}
for f in $(find kubernetes bootstrap -name '*.sops.yaml'); do
  n=$(grep -c "recipient:" "$f")
  echo "$f: $n recipients"
done | grep -v ": 1 recipients"
```

Expected: no output (every file now shows exactly 1 — the PQ recipient only).

Note the distinction: this step drops the classical recipient from the *outer* SOPS encryption (the ciphertext wrapping) of every file, `sops-age.sops.yaml` included. It deliberately does **not** touch the *inner* plaintext content of `sops-age.sops.yaml` (the `age.agekey` field set in Task 4) — that still holds both the classical and PQ private keys, so Flux's in-cluster decryption capability stays dual indefinitely as a rollback path, even though nothing is encrypted to the classical recipient anymore after this step.

- [ ] **Step 3: Commit and push**

```bash
git add .sops.yaml kubernetes bootstrap
git commit -m "feat(sops): cut over to post-quantum-only age encryption"
git push
```

- [ ] **Step 4: Final verification — confirm Flux decrypts PQ-only ciphertext cleanly in production**

```bash
flux get kustomizations -A
kubectl -n flux-system logs deploy/kustomize-controller --since=15m | grep -i "error\|decrypt" || echo "no decrypt errors"
```

- [ ] **Step 5: Update plan status to "Task 7 complete — migration cut over on <date>", pause for confirmation before Task 8.**

---

### Task 8: Resolve the talos/talsecret.sops.yaml follow-up (non-blocking due diligence)

This closes the open question Global Constraints flags, rather than leaving it as a silent permanent exclusion. Doesn't block or depend on Tasks 1–7.

**Files:** none modified — investigation only, outcome recorded in Task 9.

- [ ] **Step 1: Check talhelper's vendored SOPS/age dependency versions**

`talhelper` (pinned `aqua:budimanjojo/talhelper = 3.1.12` in `.mise.toml`) is a Go binary that vendors its own `github.com/getsops/sops/v3` (and transitively `age`) dependency, decoupled from the `sops`/`age` CLI versions this plan pins:

```bash
curl -sL https://raw.githubusercontent.com/budimanjojo/talhelper/v3.1.12/go.mod | grep -E "getsops/sops|filippo.io/age"
```

- [ ] **Step 2: Decide based on the versions found**

If both are at/above the PQ-support floor (`filippo.io/age` v1.3.0+, `getsops/sops/v3` v3.12.1+ — the same thresholds confirmed for `kustomize-controller` earlier in this migration's research), talos secrets can be migrated the same way in a follow-up plan. If either is older, record it as a known residual classical-only risk with the blocking dependency version.

- [ ] **Step 3: Update plan status to "Task 8 complete" with the finding (compatible + follow-up filed, or incompatible + tracked), pause for confirmation before Task 9.**

---

### Task 9: Final documentation pass and cleanup

**Files:**
- Modify: `docs/sops-post-quantum.md`
- Delete: `docs/sops-age-post-quantum-migration-plan.md` (this file)

- [ ] **Step 1: Consolidate the real migration record into `docs/sops-post-quantum.md`**

Replace its "Migration checklist" section (currently theoretical) with what actually happened: date, the fact it's live in production, the dual-recipient rollover procedure used, the Task 8 talhelper/talos finding, and a reminder to store a durable offline backup of `age.key` (now dual-identity and the sole local copy of both private keys outside the cluster) — plus the one operational gotcha worth remembering for next time (whether `kustomize-controller` needed a restart to pick up the updated `sops-age` secret, from Task 4 Step 4).

- [ ] **Step 2: Remove this plan file now that it's consolidated**

```bash
git rm docs/sops-age-post-quantum-migration-plan.md
git add docs/sops-post-quantum.md
git commit -m "docs: consolidate post-quantum SOPS migration record"
git push
```

- [ ] **Step 3: Offer to open a PR / merge the branch, or clean up the worktree if already merged, per user preference (`.agents/skills/git-worktree-isolation/SKILL.md` documents the cleanup: `git worktree remove` + `git branch -D`).**

- [ ] **Step 4: Mark plan complete.**
