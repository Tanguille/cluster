# Qui automations

Rules are stored in Qui's database on the `qui` PVC and managed through its UI/API.

## qBitrr cutover

On instance `main`, rules 10 and 11 pause incomplete `tv-sonarr`/`radarr`
downloads below 500 GiB free on `/media/Downloads`, then resume only torrents
tagged `qui-low-space` at 550 GiB. The tag is cleared after downloading resumes
with at least 500 GiB free. Untagged manual stops are not resumed; remove the tag
to override a guard-owned stop.

**Enable both rules and turn dry-run off before merging the qBitrr removal.**
They were saved disabled on 2026-10-25. Existing cleanup/cross-seed rules remain.

The guard does not reserve pending download sizes. qBitrr-specific recovery,
failed/recheck categories and sample/extra filters are retired; daily Arr
missing/upgrade searches are not scheduled. Cleanrr, Deduparr, Unpackerr and
native Arr handling remain.

Uninstalling qBitrr deletes its 2 GiB config PVC, not shared NFS media.
Back up its state first if needed. To roll back, restore the qBitrr manifests
from git, disable the Qui guard and review any guard-tagged stopped torrents.
