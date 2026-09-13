# OpenCode VM Workspace Migration Plan

**Goal:** Move all non-secret state from both OpenCode instances into the existing Kubernetes deployment without losing Kubernetes history, exposing credentials, or losing Git state. Keep the VM route and source files unchanged through acceptance; cleanup is a separate approved change.

## Current verified state (2026-09-06)

- VM OpenCode 1.18.29 has an active systemd service and interactive process. The repo is `/home/tanguille/cluster`, branch `main`, HEAD `1523a82cb`, with 22 worktrees and a 246 MB repository payload.
- VM OpenCode SQLite is `/home/tanguille/.local/share/opencode/opencode.db` (2,740,469,760 bytes), with WAL and SHM. Read-only integrity passed and foreign-key violations were zero. An earlier point-in-time count found 8,585 sessions, 3 projects, 27 project directories, 75,588 messages, and 291,063 parts; later inspection found schema version 108, 20 application tables, and live counts of 8,588 sessions, 75,664 messages, 291,526 parts, 1,043 todos, and 574,949 events. Capture final counts after writers are stopped.
- The Kubernetes deployment is OpenCode 1.18.29 at the same pinned digest, currently 1/1/1/1, running as UID/GID 0. Its `opencode` RWO 20 GiB PVC has 5,250,588,672 bytes used and 15,690,080,256 bytes available. The Kubernetes DB is 23,154,688 bytes plus WAL/SHM.
- `/home/tanguille/cluster` and `/home/opencode/workspace/cluster` are absent in the current container. The container lacks git, Python, SQLite, Bun, and Node, so migration inspection/import requires a temporary maintenance workload.
- The PVC contains existing `.cache` (~3.32 GiB), `.npm` (~1.48 GiB), `auth.json` metadata, and persisted `opencode.jsonc` files. Their disposition must be decided before acceptance; values must not be read or copied.
- Export/import commands exist. The existing SnapshotPolicy is Ready/Verified and its latest successful snapshot is `ai/opencode-20260906151100`, Kopia ID `684787cfbdd44bc838a527cf5788695d`. `ClusterRepository` reports `IndexBlobHealth=False/TooManyIndexBlobs` and requires validation/acceptance.
- VM Service, EndpointSlice, and HTTPRoute remain managed by GitOps. The ConfigMap JSONC already references `{env:LITELLM_API_KEY}`; the HelmRelease still uses `${LITELLM_API_KEY}` and no app Secret exists.

## Non-negotiable gates

- [ ] Preserve the existing Kubernetes database in place. Never blindly replace it or perform a blind/bulk SQL union. A schema-aware graph merge is allowed only into an isolated clone, after complete schema/file inventory and successful rehearsal; Kubernetes application rows must remain unchanged and secret-bearing rows must not enter the merge.
- [ ] Keep evidence in an external directory outside every pod-mounted path. Never print, log, decrypt, expose, or commit credentials, `auth.json`, `mcp-auth.json`, kubeconfigs, keys, tokens, or other secret values. Controlled plaintext session exports are permitted only in the bounded staging exception in Phase 4.
- [ ] Get explicit approval before live reconcile/apply, SOPS work, downtime, PVC deletion, or restore. Do not commit or push.
- [ ] Preserve unrelated worktree changes: never reset, clean, stash, or modify them. Keep VM route/source files until acceptance.

## Phase 1 — Protect the baseline and obtain approval

- [ ] Record branch, HEAD, status, all worktree paths, and the pre-existing untracked `./.ignore`, `./.slim/`, and `docs/superpowers/` state in external evidence. Make repository changes only in an isolated implementation worktree.
- [ ] Record the current Deployment, HelmRelease, Kustomization, PVC, SnapshotPolicy, ClusterRepository, Service, EndpointSlice, HTTPRoute, and snapshot status. Treat the successful snapshot above as preflight evidence, not the final backup.

**Gate:** [ ] Obtain approval for the GitOps changes, maintenance downtime, SOPS operation, final snapshot, and any later PVC restore/delete.

## Phase 2 — Measure capacity and inventory required state

- [ ] Measure PVC capacity after existing state. Size the VM DB backup, exports, Git bundle, workspace reconstruction, archive, and disposable-restore requirements. Current free space is 15.69 GB and the repo is 246 MB, but VM DB/backups/exports are not sized for PVC use. Do not require expansion now or use `.cache`/`.npm` cleanup as a shortcut.
- [ ] Inventory exact worktree paths, HEADs, branches/detached states, required refs/OIDs, index, staged/unstaged/untracked state, and the repository’s actual special features. Conditionally preserve submodules, sparse checkout, and LFS only if observed.
- [ ] Inventory required non-secret files and all external state from both instances. Record paths, types, sizes, checksums, and counts; do not perform exhaustive timestamp/hash/LFS/ignored scans unless the inventory shows they are needed.
- [ ] For both databases, complete a metadata-only inventory of schema versions, tables, columns, primary/unique keys, foreign keys, JSON references, and external-state references. Classify every table and file as merge, exclude, or archive; classify auth/account/credential/share state as excluded or encrypted for external archive. Do not read or copy secret values.
- [ ] Baseline both DB sizes, integrity/foreign-key results, final stopped-state counts, and application-row identities. If the current image cannot run SQLite, collect this from the maintenance workload rather than assuming tools exist.
- [ ] Record PVC capacity and denylisted credential metadata (path/classification, size, and safe metadata only), including existing `auth.json` and persisted configs. Decide whether each auth/config file is retained, replaced, excluded, or archived before acceptance.

## Phase 3 — Prepare the GitOps target, tooling, and secrets

- [ ] In Git only, add `workingDir: /home/tanguille/cluster`; retain the `/home/opencode` mount and add the existing PVC with `advancedMounts` and subPath `workspace/cluster` mounted at `/home/tanguille/cluster`.
- [ ] Add a temporary GitOps-owned `spec.values.controllers.app.replicas: 0` override. Keep the Deployment at zero for mount inspection, final snapshot, restore test, rehearsal, and graph merge. Do not use `exec deploy/opencode` for migration.
- [ ] Define a temporary maintenance workload that mounts the existing PVC read-write only while the Deployment is GitOps-held at replicas 0. It must contain OpenCode 1.18.29, git, python3, and sqlite3. Keep migration evidence outside the PVC.
- [ ] Change only the remaining credential gap to app-scoped `secretKeyRef` entries and create/register the encrypted app Secret through the approved SOPS workflow. Do not rewrite the JSONC’s existing `{env:LITELLM_API_KEY}` reference. Never fabricate or expose plaintext secret data.
- [ ] Render and review the GitOps changes without applying them. Keep VM Service, EndpointSlice, HTTPRoute, and source files in place.

## Phase 4 — Quiesce, back up, reconstruct, and snapshot

Perform these steps in order:

1. [ ] **Quiesce:** After approval, stop VM OpenCode and all known Kubernetes/workspace/database writers. Capture final stopped-state counts. If technically required, stage plaintext exports only as one bounded batch/session at a time; encrypt and hash them, never log or commit them, and clean them up immediately after use.
2. [ ] **Back up:** Create consistent VM and Kubernetes SQLite backups with `.backup` or `VACUUM INTO`, then run `PRAGMA integrity_check` and `PRAGMA foreign_key_check`. Checkpoint the Kubernetes DB first and retain its pre-import backup and row baseline.
3. [ ] **Reconstruct:** Complete Phase 5’s verified Git bundle and exact workspace/worktree reconstruction before taking the final snapshot. Do not snapshot an absent workspace.
4. [ ] **Stop maintenance writers:** Terminate or hold any maintenance workload and other writers after reconstruction, leaving the reconstructed workspace and destination DB quiescent.
5. [ ] **Final snapshot:** Use the existing backup policy; do not rebuild it. Validate the repository’s `IndexBlobHealth=False/TooManyIndexBlobs` limitation and record its acceptance impact. Trigger a new stopped-state snapshot of the reconstructed workspace and destination DB, wait for readiness, and record the exact Snapshot CR name/UID, `snapshotRef`, `.status.snapshot.identity`, and `.status.snapshot.kopiaSnapshotID`. Do not substitute a policy-offset selector.
6. [ ] **Restore test:** Restore-test that exact final snapshot into a disposable verification PVC. Require Restore `Completed`, `Ready=True`, PVC `Bound`, SQLite checks, and representative workspace/session checks before any merge.

## Phase 5 — Reconstruct the exact Git repository and worktrees

- [ ] Freeze the approved worktree list. Create temporary refs for detached or otherwise unreachable required OIDs, recording each ref and original OID. Create and checksum a Git bundle containing required refs, temporary refs, and observed submodule/LFS evidence; verify every required OID.
- [ ] Reconstruct the target only from the verified bundle, with no network or source fallback. Create the exact approved worktree paths and restore recorded branches/detached OIDs, index state, staged/unstaged patches, and approved non-secret untracked files.
- [ ] Reapply observed submodule, sparse-checkout, and LFS state conditionally. Do not bulk-copy ignored files or credentials.
- [ ] Complete the independent-SSH handoff using the reconstructed workspace and recorded non-secret state; keep the VM route and source files available until acceptance.
- [ ] Compare paths, HEADs, refs, index, staged/unstaged/untracked state, and required non-secret files against the external inventory using ordinary Git/Python/SQLite commands; no bespoke migration verifier is required.

## Phase 6 — Rehearse and merge the complete non-secret graph

- [ ] Rehearse OpenCode 1.18.29 behavior against an isolated clone of the Kubernetes DB. The primary path is a deterministic, schema-aware, two-pass graph merge—not CLI session import: first map primary/unique keys and classify conflicts, then merge source entities once while rewriting declared and logical references.
- [ ] Deduplicate only canonical-identical rows after comparison. Remap divergent source IDs without changing destination rows. Rewrite declared foreign-key, logical, and JSON references with schema-aware parsers, never regex. Handle project/path conflicts, singleton conflicts, and event-sequence conflicts explicitly.
- [ ] Exclude secret/global auth rows and derived or migration tables. Preserve all other classified non-secret application state, including events, todos, context, and input state. Maintain an external ledger of source entities, mappings, row counts, collision decisions, checksums, and results.
- [ ] Validate every source entity exactly once, all references, counts, hashes, integrity, foreign keys, and unchanged pre-existing Kubernetes application rows after rehearsal and production merge. Stop on any mismatch and restore the pre-import backup before retrying.
- [ ] CLI export/import may be used only as an oracle or selected-session tool for compatibility checks; it is not the migration mechanism and must not be assumed to preserve unsupported attachments or internal rows.

## Hard no-go gates

- [ ] Do not proceed with unclassified tables, files, references, unresolved collisions, failed secret separation, unsupported external state, or any mutation of a pre-existing Kubernetes row.
- [ ] Do not proceed after any count, hash, reference, integrity, foreign-key, or complete-entity-validation failure. Restore the recorded pre-import backup or exact snapshot before retrying.

## Phase 7 — Start the service and accept

- [ ] After the graph merge and all checks pass, make a separate approved GitOps change removing the temporary zero-replica override or setting replicas to 1. Reconcile only after approval; verify one ready Deployment/Pod.
- [ ] Validate the exact OpenCode 1.18.29 startup, working directory `/home/tanguille/cluster`, both mounts, workspace/worktree state, project/session listing, session reopen/create, tool execution, and a controlled write/read/delete test. Check service/route health and logs without credential output.
- [ ] Validate credential references by rendered manifests and metadata only. Confirm app-scoped `secretKeyRef` use and decide the disposition of PVC `auth.json` and persisted configs before acceptance.
- [ ] Record acceptance evidence for repository/worktrees, DB integrity and row preservation, sessions, events, todos, context/input state, workspace, external snapshot/storage files, credentials, health, and backup restore. Take and restore-test a post-migration snapshot. Keep the VM stopped and retain route/source files until the owner approves acceptance.

## Phase 8 — Separate cleanup and rollback

- [ ] After acceptance, obtain a separate approval/change to remove only the VM-specific Service, EndpointSlice, and HTTPRoute. Do not remove the Kubernetes route, PVC, DB, source, or evidence during migration.
- [ ] On failure, terminate the maintenance workload and hold/suspend the HelmRelease and Kustomization **before** stopping/deleting/restoring. Keep all writers stopped and reconciliation held. Create/apply an exact recorded snapshot Restore through GitOps; do not use the current policy-offset Restore as migration rollback.
- [ ] Wait for Restore `Completed` and `Ready=True`, PVC `Bound`, and inspection of the restored PVC before resuming reconciliation. Restore the pre-import DB backup instead when only the graph merge failed and the PVC is sound. Resume only after the restore is verified, then keep the VM stopped until reapproval.

## Focused validation

- [ ] `git diff --check` passes for this plan and for the eventual GitOps change.
- [ ] Repo/worktree paths, HEADs, refs, index and file-state checks pass; bundle checksum and `git bundle verify` pass.
- [ ] VM and Kubernetes SQLite backups pass integrity and foreign-key checks; existing Kubernetes rows and counts remain unchanged.
- [ ] Every source entity is represented once in the merge ledger; workspace, events/todos/context/input-state, external-storage, credential-reference, health, and route checks pass.
- [ ] Exact final and post-migration snapshots restore successfully, with the ClusterRepository index-health limitation explicitly accepted.

## Known unknowns before execution

- Destination schema, counts, and final stopped-state inventory.
- Exact schema-aware merge mappings, collision handling, and external-state references.
- Concrete PVC-mounted maintenance workload, OpenCode 1.18.29 image/tooling, and any bounded export staging required.
- Capacity after backups, bundle, workspace, merge, and restore material.
- Disposition of PVC auth/config files and authentication behavior.
- Snapshot repository health and acceptance of `IndexBlobHealth=False/TooManyIndexBlobs`.
