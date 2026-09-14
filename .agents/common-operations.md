# Common Operations

**When to use:** validation, tooling, add app, new application, upgrade, SOPS, secrets, encrypt, debug, troubleshooting, logs, backup, restore, kopiur, snapshot.

Also serves as the authoritative command reference linked from `docs/useful_commands.md`.

Step-by-step procedures for frequent cluster tasks.

## Validation and tooling

- Run `flux`, `helm`, `kubectl`, `kustomize`, `flate`, `sops`, `age`, `talosctl`, `minijinja-cli`, `yq`, `jq`, and `shellcheck` through `mise exec -- <command>`.
- Kubernetes or mixed changes: `bash .agents/skills/pr-review/scripts/validate-pr.sh` (flate — renders Helm charts, not just Kustomization YAML — and shellcheck).
- Shell-only changes: `mise exec -- shellcheck` on every touched `*.sh`.
- Documentation-only changes: run `git diff --check` and verify every changed local reference exists.

### Pre-commit: `oxfmt not found` (agent box)

Lefthook (`.lefthook.toml`) runs **bare** `oxfmt` on staged `*.yaml`/`*.yml` (excluding
`*.sops.yaml`) and `*.json*`. On the agent box `oxfmt` is a mise tool and **not on PATH**, so the
hook fails with "oxfmt not found" even though the code is fine. Verified facts:

- `mise` itself is not on PATH in non-login shells; tool binaries live under
  `/opt/data/home/.local/share/mise/installs/` (with `shims/` alongside).
- The repo pins `oxfmt = "0.67.0"` (`.mise.toml`) but the agent box has `0.66.0` installed —
  the local binary lags the pin.

Fixes, in order of preference:

1. **Commit in a mise-active shell** so the pinned version resolves: `mise exec -- git commit …`
   (after `mise trust` in the worktree — a fresh worktree's `.mise.toml` is untrusted, which also
   breaks the shims: `mise ERROR Config files ... are not trusted`).
2. **Put an installed oxfmt on PATH** before committing (this is what actually unblocked the
   kguardian PR):

   ```bash
   # 0.66.0 is what's actually installed on the agent box (the repo's .mise.toml pins
   # 0.67.0 — if the pinned version is installed, use that instead); verify with:
   #   ls /opt/data/home/.local/share/mise/installs/oxfmt/
   export PATH="/opt/data/home/.local/share/mise/installs/oxfmt/0.66.0/node_modules/.bin:$PATH"
   git commit -m "..."
   ```

If formatting output differs between the local 0.66.0 and the pinned 0.67.0 (CI uses the pin),
prefer option 1 so local and CI agree.

## Ceph: `crash ls-new` hides archived crashes, not old ones

`ceph crash ls-new` filters on exactly one thing, whether a crash is archived (`crash/module.py`
`do_ls_new`); age plays no part. `mgr/crash/warn_recent_interval` (86400s here) only gates the
`RECENT_CRASH` health warning, so it is **not** what bounds this list. The oldest entry is the floor
of "since the last `crash archive`/`archive-all`", not when the problem started -- which is how a
first-seen date read off it under-reported an age by eight months. Use `ceph crash ls`, which lists
archived crashes too, and `ceph crash info <crash-id>` for the real timestamps.

## Shell: statuses `set -e` does not see

`set -euo pipefail` does **not** catch a failure in a process substitution, or in a command
substitution used as an argument. The producer's exit status is discarded and the consumer runs on
partial or empty input, so the script succeeds and emits a plausible-looking artifact.

Four instances of this were found in one evening, in two people's code:

| Shape | What it produced |
| --- | --- |
| `talosctl machineconfig patch <(render ...)` | a machine config with `machine.type` missing, ready to `apply-node` |
| `minijinja-cli ... <(sops -d ...)` | a config rendered against an empty secret context |
| `installer/$(yq '.id' ...)` | the literal string `null` baked into an install image |
| `mapfile -t args < <(yq ...)` | an installer published with no kernel args and no extensions |

Write it as a bare assignment instead, so `set -e` aborts before anything downstream runs:

```bash
value="$(producer ...)"          # aborts here on failure
[ -n "$value" ] || { echo "producer returned nothing" >&2; exit 1; }
consumer <(printf '%s' "$value")
```

Assigning first also means a failure emits **nothing** to stdout. That matters when a caller pipes
the output somewhere consequential: `wait $!` on the process substitution recovers the status, but
only after the consumer has already streamed a partial result to whoever was reading.

It bites **verification code** too, and there it is worse: a check that reads the wrong status
reports a false PASS, laundering the bug as verified. `rc=$?` after a pipe reads the last stage,
not the one that failed — use `${PIPESTATUS[0]}`, or do not pipe the command under test.

Two related traps in the same family:

- A tool that exits 0 while producing nothing useful. `yq` prints `null` and exits 0 for a missing
  key; use `yq -e`. `jq -r '.id'` prints `null`; use `jq -er '.id | strings | select(length > 0)'`.
- `eval "$(cmd)"` reports **eval's** status, not `cmd`'s. Capture, check, then eval.

## Adding a new application

Use [add-app-to-cluster](skills/add-app-to-cluster/SKILL.md) skill for full procedure.

1. For a new namespace, create `kubernetes/apps/<namespace>/kustomization.yaml` with `namespace: <ns>` and component `../../components/common`; existing namespaces need no namespace step
2. Add OCIRepository if external
3. Create app in `kubernetes/apps/<namespace>/<app>/`
4. Add Kustomization in appropriate `ks.yaml`
5. Run validation on the new app: `bash .agents/skills/pr-review/scripts/validate-pr.sh` (or `mise exec -- flate test all` directly, which renders the HelmRelease too — `kustomize build` alone doesn't)

## Secrets management (SOPS)

### Where SOPS actually runs — the agent box has no age key

Decrypt / encrypt / re-encrypt **cannot run on the agent box**: the config root has no `age.key`
(verified: `/opt/data/cluster/age.key` is absent), so every SOPS op must run **on the management
host**. The age key lives at `tanguille@192.168.0.181:~/cluster/age.key`.

The `k8s-management` ssh alias is **dead** in this environment: the user's passwd home is
`/opt/data` while `$HOME=/opt/data/home`, so OpenSSH reads `/opt/data/.ssh/config` (key only, no
config) and never sees the alias defined in `/opt/data/home/.ssh/config`. Until that is fixed,
always connect explicitly:

```bash
ssh -i /opt/data/.ssh/id_ed25519 tanguille@192.168.0.181 '<cmd>'
```

On the remote, `sops` (and `age`, `kubectl`, …) are **mise shims**, so a bare `sops --version`
prints nothing. Prefix with `mise exec -- sops …` (or invoke the shim under a trusted `cwd` that
has a trusted `.mise.toml`).

### Recipients differ per subtree (read `.sops.yaml`)

Do not assume one key. `.sops.yaml` maps path → age recipient:

| Path | Key (first 10 chars) |
|------|----------------------|
| `talos/**/*.sops.yaml` | `age12gul5m0…` |
| `(bootstrap\|kubernetes)/**/*.sops.yaml` | `age1pq1f69…` (post-quantum) |

A CloudNativePG role secret under `kubernetes/` uses the `age1pq1…` key; a `talos/` file uses
`age12gul5m0…`. Encrypting with the wrong recipient (or hand-adding a `sops:` block) breaks
decrypt for the real owner.

### The three SOPS traps (each cost real time — avoid them)

1. **`stringData` → `data` on decrypt/encrypt.** SOPS round-trips convert `stringData` keys into
   `data`. Text-editing a decrypted file and re-encrypting can silently drop or mis-key
   `stringData` entries. Rebuild via a **dict** in Python (load the encrypted YAML, set the
   value, re-encrypt) rather than text-splicing.
2. **Stale `sops:` footer after a textual edit.** If you text-edit an encrypted file (or a
   decrypt→edit), the old `sops:` metadata block remains and `sops --encrypt` fails on it. Always
   **decrypt first**, edit the clean file, then encrypt — never edit the ciphertext in place.
3. **Missing `--input-type yaml` makes SOPS guess JSON and fail.** Always pass
   `--input-type yaml --output-type yaml` on YAML files; do not rely on extension sniffing.

### Canonical flow (new secret value, remote)

1. `sops --encrypt` is a no-op on already-encrypted content; for a **new** value, decrypt the
   existing file to a temp, set the key in a dict, and re-encrypt with the correct recipient:

   ```bash
   # on the management host, in a trusted cwd (a worktree with .mise.toml)
   mise exec -- sops --input-type yaml --output-type yaml -d file.sops.yaml > /tmp/plain.yaml
   # edit /tmp/plain.yaml (dict-based if it has stringData), then:
   mise exec -- sops --input-type yaml --output-type yaml -e /tmp/plain.yaml > file.sops.yaml
   rm /tmp/plain.yaml   # never leave a decrypted secret on disk
   ```

2. Verify the recipient line in the resulting `sops:` block matches the subtree above.
3. `rm` the decrypted temp **immediately** (approval-gated; ask the user).

### Standing rules

- Never commit plaintext secrets or the age key. Use placeholders so the user adds values manually.
- **Ask before decrypting/editing SOPS** (AGENTS.md) and before `rm`-ing a decrypted temp.
- Post-quantum age (`age1pq1…`) is supported. Both halves of a key must be in `age.key`: a key
  that lost its PQ half decrypts `talos/` but fails on everything under `kubernetes/`.
- The remote `~/cluster` checkout may be on a **stale feature branch** (it was 18 commits behind
  `origin/main` during the kguardian work). `git fetch` there before trusting its state; SOPS
  only needs the `.sops.yaml` + `age.key`, not a fresh tree, but re-encrypting against a stale
  tree can carry in stale content — prefer editing the specific file.

## PR shepherding (re-shepherd pass)

Standing order: iterate an owner PR until CI + automated review are clean. One pass:

1. **`git fetch origin` first** (hard rule — this worktree sits on a feature branch and
   is always stale; never answer current-state questions from it).
2. Read the PR via ToolHive `github_pull_request_read` (`method: get`): state (`open`/
   `draft`), head SHA, base SHA, `mergeable`, `mergeable_state`, `merge_state_status`,
   `commits` count, diffstat.
3. **Base moved?** If `base.sha != origin/main` head: compare overlap —
   `git diff --name-only <pr-head>...origin/main` vs the PR's file list. Overlap files
   are rebase candidates; dry-run with `git merge-tree <pr-head> <pr-head> origin/main`
   (or `git merge-tree --write-tree <pr-head> origin/main`) to confirm clean.
4. **Rebase = Gate A, server-side only**: `github_update_pull_request_branch` with
   `expectedHeadSha` = the current head SHA (guards against concurrent pushes). **No
   local `git push`** — the agent box has no GitHub token by default; branch updates go
   through ToolHive `push_files` (full-file contents, no delete) or the server-side
   rebase.
5. `mergeable_state: "unknown"` right after a base move usually just means GitHub is
   recalculating — confirm with the rebase rather than looping on polls.
6. Re-poll `get_check_runs` until the new head is `success`. Normal draft-green shape:
   ~12 success + 2 skipped (CodeRabbit/DeepSource skip on drafts).
7. Read comments for *new* feedback since the last pass; address or answer it.
8. **Budget: 3 fix cycles** per issue class, then escalate to the owner with evidence
   (log excerpts, which checks failed, and the classification: flake vs diff vs
   baseline) — don't silently keep retrying.
9. **Hard no-s without explicit per-instance owner approval:** merging the PR,
   pushing to `main`, force-pushing any branch, `cluster-apply`, decrypting secrets
   into a PR description/log/chat.
10. Status wording: report `mergeable_state`/`merge_state_status` verbatim; do not
    imply "clean" while either is `unknown`/`behind`.

## Debugging

Use [debug-cluster](skills/debug-cluster/SKILL.md) skill for structured 5-Whys analysis and troubleshooting.

## Backup & Restore

Use [backup-restore](skills/backup-restore/SKILL.md) skill for kopiur Kopia operations.

## Other skills

See the [skill catalog](../AGENTS.md#load-context-on-demand) for git-worktree-isolation, k8s-at-home-research, pr-review, cluster-sops, and prometheus-cluster-health.
