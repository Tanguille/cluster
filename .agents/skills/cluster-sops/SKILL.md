---
name: cluster-sops
description: >-
  Decrypt, create, edit, and re-encrypt SOPS secrets for this repo — on the agent
  box, where its own age key now lives (one local command, no SSH), with the remote
  management host as fallback.

  user: "add a new secret for app X" → local sops encrypt with the .sops.yaml recipients
  user: "change the DB password" → decrypt → edit in dict form → re-encrypt
  user: "re-encrypt after changing recipients" → sops updatekeys

  Use proactively whenever a *.sops.yaml value must be created or changed. Ask the
  owner first — decrypting/editing SOPS is an ask-first operation per AGENTS.md.
compatibility: `sops` (aqua, 3.13.3) + the agent-box age key at ~/.config/sops/age/keys.txt; the repo's `.sops.yaml` in the worktree. Fallback: `ssh -i /opt/data/.ssh/id_ed25519 tanguille@192.168.0.181` (remote key at ~/cluster/age.key).
---

# Cluster SOPS (local-first secrets workflow)

## Where it runs — and why (2026-09-14: SSH no longer required)

- **The agent box holds an age key file** at `~/.config/sops/age/keys.txt`
  (mode 600, inside 700 directories) with **3 identities**: the agent-box's own
  revocable PQ key **plus the two master keys** (`age12gul5m0…`, `age1pq1f69…`)
  copied from the remote 2026-09-14. It therefore decrypts **every** file in the
  repo locally — verified on real `kubernetes/` and `talos/` files, no SSH.
  No existing file was re-encrypted and no recipients were changed.
- **The key is auto-discovered — no env var required on this box (verified: a
  decrypt succeeds with `SOPS_AGE_KEY_FILE` unset).** sops 3.13.3 loads the age
  key from `sops/age/keys.txt` under the user config directory —
  `$XDG_CONFIG_HOME/sops/age/keys.txt`, or `$HOME/.config/sops/age/keys.txt`
  when `XDG_CONFIG_HOME` is unset. The export below is only needed if the key is
  ever moved *off* that resolved path (then it is required, or you get
  "no identity matched"):

  ```bash
  export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/keys.txt"
  ```

- **Local sops binary** (aqua install; the mise shims are unreliable in non-login
  shells, and bare `sops` may fail before sops even runs — use the full path in
  every canonical example below):
  `/opt/data/home/.local/share/mise/installs/aqua-getsops-sops/3.13.3/sops`.
  `age-keygen` (for key ops) is at
  `.../aqua-filo-sottile-age/1.3.2/age/age-keygen` — note its `-y` takes the
  identity file as a *positional* argument (no `-r` flag), and the `age` binary
  in the same directory is the encrypt/decrypt CLI (no `-g` flag).

### Fallback: the remote management host

Only needed if the master keys are ever removed from `keys.txt` (to shrink the
agent box's blast radius — a pre-copy backup is at `keys.txt.bak`). Then SOPS
decrypt falls back to the management host, where the original key lives
(`tanguille@192.168.0.181:~/cluster/age.key`):

- The `k8s-management` ssh alias is a **phantom** here: passwd home is `/opt/data`
  but `$HOME=/opt/data/home`, so OpenSSH reads `/opt/data/.ssh/config` (absent) and
  the alias never resolves.
- Remote `sops` is a **mise shim** — bare `sops --version` can print nothing; use
  `mise exec -- sops …`.
- Remote `~/cluster` may sit on a **stale feature branch** (it was on
  `feat/truenas-mcp` during the kguardian work). `git fetch` + check the branch
  before trusting file state there.
- Remote shell is **fish**: pipe complex commands via `ssh … 'python3 -' < local.py`,
  never multi-line heredocs.

## Recipients (from `.sops.yaml` — read it, don't trust memory)

| path_regex | recipient |
|---|---|
| `talos/.*\.sops\.ya?ml` | `age12gul5m0…` (plain X25519) |
| `(bootstrap\|kubernetes)/.*\.sops\.ya?ml` | `age1pq1f69…` (post-quantum) |

`sops` picks the rule by **path** — run it against the file's real in-repo path
(or with the file inside the matching subtree). A path outside the rules (e.g.
`/tmp/...`) matches **no** creation rule and fails with "no matching creation
rules found", even with an explicit `--age` — the `.sops.yaml` config wins over
CLI flags (verified on 3.13.3).

**Key inventory (2026-09-14, current truth):** `~/.config/sops/age/keys.txt` holds
**3 identities** — the agent-box's own revocable key (`age1pq1hzp…`) plus the two
master keys copied from the remote (`age12gul5m0…`, `age1pq1f69…`). No existing
file was re-encrypted or had its recipients changed ("don't touch existing
encryption" — honored). Consequences:

- **Decrypt/edit any existing file: works locally**, no SSH (verified on real
  `kubernetes/` and `talos/` files).
- **New files encrypt to the same recipients as before** (driven by `.sops.yaml`)
  — the agent-box key is a *redundant* decryptor, not a recipient, unless you
  later decide to add it to `.sops.yaml` + `updatekeys`.
- **Trade-off accepted by the owner:** the master keys now live on 2 boxes. If
  the agent box is compromised, *rotate the master* (the revocable-agent-key
  model is no longer the only blast-radius control). If you'd rather not keep
  that exposure, the master lines can be removed from `keys.txt` (a backup of
  the pre-copy file exists at `keys.txt.bak`) — existing-file decryption would
  then fall back to SSH.

**PQ note (learned the hard way):** "post-quantum" is a key *format*
(ML-KEM-768), not a shared secret — two different `age1pq…` keys do NOT interop.
A brand-new PQ key cannot decrypt files encrypted to an older PQ key.

## The three traps (each already cost time — avoid them)

1. **`stringData` vs `data` on round-trips.** SOPS does **not** convert one field
   into the other — `encrypted_regex: ^(data|stringData)$` only encrypts whichever
   section already exists — and this repo's `kubernetes/` and `talos/` secrets use
   **`stringData`** (there is no `data`), so blind `d["data"][key] = …` raises
   `KeyError` on them. **Fix:** rebuild with a Python dict — load the decrypted
   YAML, write the value into whichever section the file already uses, dump back —
   instead of hand-editing text.
2. **Stale `sops:` footer.** Text-editing an already-encrypted file (or a decrypt
   that left the metadata block) makes the next `sops encrypt` **fail** on the
   existing top-level `sops` entry ("top-level entry called 'sops'", rc 203) — it
   is *not* a no-op. **Fix:** always start from a *fully decrypted* file (metadata
   stripped) before re-encrypting; never edit the ciphertext by hand.
3. **Missing `--input-type`/`--output-type`.** Without both flags sops may guess
   JSON and fail on YAML secrets ("error: no matching creation rules found" or a
   JSON-guess failure). **Fix:** pass both flags explicitly for `sops encrypt`
   and `sops decrypt`. Use the supported flags documented for `sops updatekeys`
   (it rejects `--output-type`).

## Canonical flows (local-first, one command each)

All examples use the full sops path rather than bare `sops` (which can fail
before sops runs in a non-login shell — the mise shims are unreliable outside a
login shell). They assume `cd` into the worktree that contains `.sops.yaml`, and
— only if the key were ever moved off its auto-discovered path — the
`SOPS_AGE_KEY_FILE` override above.

```bash
SOPS=/opt/data/home/.local/share/mise/installs/aqua-getsops-sops/3.13.3/sops
```

### New secret value

```bash
F=kubernetes/apps/<ns>/<app>/<name>.sops.yaml   # path MUST match a .sops.yaml rule
# stage the file with data: {key: base64value} or stringData: {key: plain}
"$SOPS" encrypt --in-place --input-type yaml --output-type yaml "$F"
# sanity: only data/stringData encrypted — SOPS ciphertext starts ENC[AES256_GCM,…
# (underscored, not a 32-char hex run), so match ENC[ — a [A-Z0-9]{32,} grep
# after ENC matches nothing on real files — plus the sops footer, and no plaintext
grep -n "ENC\[" "$F" | head
```

### Change an existing value (dict-based, no text surgery)

The re-encrypt **must** target the rule-matching in-repo path (a `/tmp` temp
matches no creation rule — see Recipients), so the edited plaintext goes back
over the file, then `encrypt --in-place`:

```bash
set -euo pipefail   # fail fast: a failed decrypt/edit must stop before `cp` overwrites the tracked file
F=kubernetes/apps/<ns>/<app>/<name>.sops.yaml
T="$(mktemp /tmp/secret.XXXXXX)"; chmod 600 "$T"   # unique mode-600 temp (no pre-create/clobber)
trap 'rm -f "$T"' EXIT                              # cleaned up on success AND failure
"$SOPS" decrypt --input-type yaml --output-type yaml "$F" > "$T"
python3 - "$T" <<'PY'
import sys, yaml
p = sys.argv[1]
d = yaml.safe_load(open(p))
# write the new value into whichever section the file ALREADY uses (data: is
# base64; stringData: is plain) — select the existing field, do not assume data:
if "data" in d:
    d["data"]["key"] = "newbase64value"
else:
    d["stringData"]["key"] = "newplainvalue"
yaml.safe_dump(d, open(p, "w"), sort_keys=False)
PY
cp "$T" "$F"   # edited plaintext back over the rule-matching path
"$SOPS" encrypt --in-place --input-type yaml --output-type yaml "$F"
rm -f "$T"   # trap also covers the failure path
```

### Re-encrypt after a recipient change

```bash
# exclude the root .sops.yaml — it is a plaintext policy file, not a SOPS document
git ls-files | grep '\.sops\.ya?ml$' | grep -v '^\.sops\.yaml$' | xargs \
  "$SOPS" updatekeys --yes --input-type yaml
```

(`updatekeys` on 3.13.3 takes `--yes` + `--input-type` and rewrites each file in
place — it has no `--in-place`/`--output-type` flags; passing them errors.)

### Add a CNPG managed role / DB secret (CNPG subtree)

Same pattern; the CNPG role secret lives under the cloudnative-pg app path (its
`managed.roles[].passwordSecret` points at it), so it encrypts under the
`kubernetes/` rule. The role itself is added in `cluster.yaml` (plaintext), the
password in the `.sops.yaml`.

## Standing rules

- **Ask before decrypting or editing any `*.sops.yaml`** (AGENTS.md), and before
  `rm`-ing a decrypted temp file.
- **Never echo decrypted values** into chat, PR descriptions, logs, or commit
  messages. Use `[REDACTED]`.
- The **master age key now lives on the agent box too** (copied 2026-09-14 by
  explicit owner decision; `keys.txt` = 3 identities). It is still not committed
  and not echoed — only *public* keys ever go into `.sops.yaml`. If you later
  want to shrink the blast radius, remove the master lines from `keys.txt`
  (backup at `keys.txt.bak`); existing-file decryption then falls back to SSH.
- After encryption: `git diff` should show **only ciphertext changes** (no
  plaintext, no `stringData`/`data` shape drift beyond what SOPS itself did).
- Commit in the agent-box worktree and follow the normal commit/PR flow (PR
  shepherding rules in `common-operations.md`). The remote is a fallback for
  SOPS + the only push path if ToolHive is down.
