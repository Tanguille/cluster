# OpenCode VM Workspace Migration Design

## Goal and approved constraints

Move the VM OpenCode workspace into the existing Kubernetes OpenCode deployment
without losing either instance's sessions, project state, Git worktrees, or
recoverable configuration. Kubernetes becomes the only active server after
acceptance. The outage is deliberately short: both services are quiesced only
for the final delta, exports, and consistent database backups. Both histories
are retained; the Kubernetes database is never replaced by the VM database.

The following decisions are fixed:

- Keep the Kubernetes OpenCode database and import every VM session.
- Preserve the VM project path as `/home/tanguille/cluster`.
- Keep non-secret runtime configuration in GitOps (ConfigMap-backed JSONC in
  `kubernetes/apps/ai/opencode/app/config/`) and inject credentials as
  SOPS-encrypted Secrets through the HelmRelease.
- Do not copy VM-global configuration, authentication files, MCP OAuth files,
  plugin caches, logs, or raw ignored credentials into the pod.
- Configuration changes are GitOps-only; this design does not include direct
  live-cluster edits.

## Target layout and capacity gate

The existing `opencode` PVC remains mounted at `/home/opencode` for Kubernetes
data and session state. Its existing database is retained. A PVC-relative
subPath **`workspace/cluster`** is mounted at **`/home/tanguille/cluster`** in
the OpenCode container, and the container `workingDir` is set to
`/home/tanguille/cluster`. This exact path preserves absolute project,
session, and worktree references.

The `workspace/cluster` directory must be precreated on the PVC and verified
before the subPath mount is enabled. Before enabling it, measure actual free
space and the copied workspace, archive staging, and temporary import
requirements. The gate is at least twice the measured copied payload free
after existing state is accounted for. If the measured free-space gate fails,
stop: capacity must be expanded through GitOps before migration. Do not solve
the gate by deleting state or adding an unmanaged volume. The existing Kopia
policy continues to back up this PVC.

## GitOps and credential inventory

Create a source-to-destination inventory with one row for every VM setting and
credential-bearing path:

| Source | Destination or disposition | Rule |
| --- | --- | --- |
| Global/server configuration | Repository ConfigMap-backed JSONC | Recreate behavior; never copy the VM file into the PVC |
| Agents, commands, modes, reviewed project `.opencode/` content | Tracked repository content | Every required item has a reviewed source |
| Provider keys, MCP credentials, server password | App-scoped SOPS Secret referenced by `secretKeyRef` | Never put plaintext in Git or the workspace |
| `auth.json`, `mcp-auth.json`, OAuth/account state | Offline encrypted archive, unless deliberately recreated as a Secret | Never mount raw files |
| `age.key`, kubeconfig, deploy keys, Cloudflare credentials, and other ignored credentials | Offline encrypted archive only | Denylisted from pod transfer |
| Plugin/npm caches and logs | Not migrated | Rebuild from declarative configuration |

Inventory both VM state and all existing Kubernetes PVC state for raw
credentials, including hidden files, ignored files, mounted paths, and
application-generated files. Complete encrypted archives and their checksums
must live outside every pod-mounted PVC. They are finalized only after all
writers stop. The inventory must classify every item as recreated through
GitOps, intentionally retired, or archived offline.

The existing Flux-substituted LiteLLM key must be migrated to an app-scoped
SOPS Secret and referenced with `secretKeyRef`. Remove the substitution from
the runtime configuration; do not retain a plaintext substitution. Verify the
Secret reference before cutover.

## Safe Git bundle and worktree procedure

Do not use a fresh clone as the migration mechanism, and do not bulk-copy a VM
worktree. Before quiescence, produce a complete Git inventory for the main
repository and **every VM worktree**, including:

1. each worktree's exact absolute path, `.git` file/directory, and
   `.git/worktrees` administrative path;
2. branch or detached-HEAD status and exact `HEAD` object;
3. all required refs and objects, including local and remote-tracking refs,
   tags, and reachable/unreachable objects needed by the inventory. Before
   bundle creation, give every required detached or unreachable object a
   temporary migration ref such as
   `refs/migration/opencode/<worktree>/<oid>`;
4. index contents, staged diff, unstaged diff, untracked paths, and ignored
   paths for each worktree; and
5. submodules, sparse-checkout, LFS pointers/objects if used, and complete
   `.git/worktrees` metadata mapping.

At quiescence, freeze Git writers, rerun the inventory, create all temporary
migration refs, and create a checksummed Git bundle containing those refs and
all other required refs and objects. Transfer the bundle and the separate
staged/unstaged/untracked manifests and patches to the approved workspace
staging area. Verify the bundle before changing the destination; merely
recording unreachable OIDs without giving them bundle refs is insufficient.

Reconstruct a clean repository solely from the verified bundle, with no network
fetch or source-repository fallback, and verify that every recorded OID is
present. Precreate the exact absolute directories for every approved worktree
under the workspace, then recreate them with `git worktree add` at those exact
paths, using the recorded branch or detached commit. Restore refs and verify
object availability from the bundle. Replay each worktree's staged patch into
its index, its unstaged patch into the worktree, and its recorded untracked
files.
Ignored files are not bulk-copied. Recreate only explicitly approved worktree
directories and files; credential denylist paths are never recreated in the
pod. Other ignored files require explicit non-credential approval and an
individual inventory entry. Verify `git status`, `git worktree list`, refs,
HEADs, diffs, indexes, and untracked manifests against the source.

## Cutover and state preservation

1. Measure capacity, precreate `workspace/cluster`, complete the Git and
   credential inventories, and pass the free-space gate.
2. Take and verify a pre-cutover Kubernetes PVC snapshot while the service is
   running; this is an additional rollback reference, not the final
   database-consistent snapshot.
3. Recreate Git worktrees from the verified bundle in the target path and
   verify the replayed state.
4. Stop both OpenCode services and all other writers to their databases and
   workspace. Confirm no process holds either SQLite database. Rerun the final
   Git and credential inventory.
5. Export every VM session to an individual JSON file. Create the encrypted,
   checksummed archives outside all pod-mounted PVCs, including VM state and
   denylisted credentials for offline recovery. Finalize them only now.
6. Back up the VM SQLite database consistently: use SQLite `.backup` or
   `VACUUM INTO`, ensure the consistent backup includes WAL contents, then run
   `PRAGMA integrity_check` and `PRAGMA foreign_key_check` on the backup. Do
   not copy the live database file and WAL independently.
7. After both services have stopped, checkpoint the Kubernetes database and
   run `PRAGMA integrity_check` and `PRAGMA foreign_key_check` against its
   consistent backup. Then trigger the final consistent snapshot with
   `kubectl kopiur snapshot now --policy opencode -n ai --wait` (or the
   repository-equivalent command). Wait for Snapshot readiness, record its
   exact Snapshot CR and Kopia identity, and restore-test that exact snapshot
   into a disposable verification PVC before any session import.
8. Configure the GitOps deployment changes, including the precreated subPath
   mount and `workingDir`, while keeping the Kubernetes Deployment stopped.
   Keep the VM stopped and unchanged.

## Safe session import

Keep the Kubernetes Deployment stopped while configuring the mount, preparing
and rehearsing imports, and performing every session import. Before the first
production import, make a consistent SQLite `.backup` or `VACUUM INTO` of the
Kubernetes database and run `PRAGMA integrity_check` and
`PRAGMA foreign_key_check` on that backup. Rehearse every import against a
disposable copy of the Kubernetes database and exported files first. OpenCode's importer is session-oriented: it
does not merge arbitrary SQLite databases, guarantee preservation of every
internal table or attachment, automatically reconcile filesystem worktree
contexts, or safely remap colliding IDs/references. Record the supported
export/import format and installed version's limitations; fields it does not
export are not assumed to be preserved.

Before each production import, preflight intersections for session, message,
and part IDs. Byte-identical collisions may be deduplicated only after content
hash comparison. Divergent collisions receive new IDs, with every
message/part/session reference remapped consistently. Import each VM session
from its original directory/worktree context, not from a common synthetic
directory. Maintain a ledger outside the runtime database containing source
path and IDs, export checksum, destination IDs, collision decision, remap map,
timestamp, importer version, and result. Validate expected rows after every
session import; a failed validation stops the process, restores the
pre-import Kubernetes database backup, and requires the failed import to be
corrected and rehearsed again before retrying.

Start Kubernetes OpenCode only after every import and all ledger, row, SQLite,
workspace, and verification checks pass. Do not delete or modify VM source
files or archives during acceptance.

## Concrete rollback

Rollback is available through the acceptance period. If staging, import, or
verification fails, stop Kubernetes OpenCode and preserve all archives and VM
files. Use exactly one restore selector, pinned to the recorded
`snapshotRef` (the final consistent snapshot). Follow the repository's GitOps
restore procedure:
suspend the OpenCode HelmRelease and its Kustomization, drain and verify all
PVC users are stopped, delete **only the affected PVC**, resume reconciliation,
and wait for Restore `Completed`, Restore `Ready=True`, and the restored PVC
`Bound`. Inspect for orphaned prime PVCs and unexpected replacement resources
before restarting the VM service and retaining its route for diagnosis. Do not
delete/recreate a Restore object while its PVC is Bound.

Only after acceptance may the VM route, VM service, and offline archives be
retired under a separate approved change.

## Verification gates

| Claim | Required evidence |
| --- | --- |
| No source state or credential omitted | Checksummed offline archive and inventories cover VM and Kubernetes PVC paths; denylist scan is clean |
| Workspace and Git are exact | Per-worktree paths, refs/objects, branches/HEADs, index, staged/unstaged/untracked state, and `.git/worktrees` metadata reconcile; representative re-export comparisons cover every worktree |
| Databases are sound | Source and destination pass `PRAGMA integrity_check` and `PRAGMA foreign_key_check`; backups include WAL consistently |
| Both histories survive | Exact per-session message/part counts and normalized content hashes match exports; collision/remap ledger is complete |
| Kubernetes history is unchanged | Comparison proves every pre-existing Kubernetes row remains unchanged, with no ID overwrite |
| Imports retain context | Representative sessions from every worktree re-export identically apart from documented IDs/normalization |
| Configuration is declarative | Every required setting has a GitOps destination; the LiteLLM key is an app-scoped SOPS Secret referenced with `secretKeyRef`; all runtime credentials use `secretKeyRef` |
| No raw credential reached a pod | Workspace and all pod mounts contain no denylisted credential path or plaintext secret |
| Runtime works | Health, project/worktree listing, file listing, session creation, tool execution, and a write test succeed at `/home/tanguille/cluster` |
| Recovery works | The final snapshot is restore-tested before import; a new Kopia snapshot is `Ready=True`; disposable restore completes, is `Ready=True`, has PVC `Bound`, and passes the same SQLite and representative-session checks |

## Non-goals

- Migrating plugin cache or logs.
- Copying raw VM credentials into Kubernetes.
- Combining two SQLite databases directly.
- Removing the VM endpoint before post-cutover acceptance.
