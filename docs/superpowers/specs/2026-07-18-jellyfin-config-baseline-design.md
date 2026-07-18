# Jellyfin configuration baseline

## Goal

Remove stale, unsupported, and unmeasured Jellyfin runtime tuning so the
deployment follows Jellyfin's documented defaults and retains only the
Kubernetes configuration required by this cluster.

## Approved changes

In `kubernetes/apps/media/jellyfin/app/helmrelease.yaml`:

- Remove supplemental groups `100` and `109`; Jellyfin runs with the required
  `video` (`44`) and `render` (`226`) groups only.
- Remove `DOTNET_SYSTEM_IO_DISABLEFILELOCKING`,
  `DOTNET_GCAllowVeryLargeObjects`, and `DOTNET_GCLOHThreshold`.
- Remove every `JELLYFIN_FFmpeg__*` override. This restores Jellyfin's
  documented defaults for `probesize` and `analyzeduration` and leaves hardware
  acceleration to the Jellyfin Dashboard.
- Remove comments that only describe the deleted tuning.

## Deliberately retained configuration

- AMD GPU node affinity, the `squat.ai/dri` extended resource, `fsGroup: 568`,
  and the `video`/`render` groups.
- The root startup wrapper, web-index ownership repair, and subsequent drop to
  UID/GID 568. Active FileTransformation plugins depend on this behavior and
  it has not been independently proven safe to remove.
- Health probes, resource requests and limits, NFS and hostPath media mounts,
  and disk-backed transient cache, transcode, log, and temporary directories.

## Evidence and expected behavior

- Jellyfin's current documentation gives `1G` and `200M` as the defaults for
  `FFmpeg:probesize` and `FFmpeg:analyzeduration`.
- A read-only comparison on representative 2160p Movies and Shows MKVs found
  identical video, audio, subtitle, and duration metadata with current and
  default settings. Reversed-order measurements found no consistent latency
  benefit from the custom values.
- The current deployment successfully detects the RDNA 4 GPU through VA-API.
  VA-API configuration remains a Jellyfin Dashboard responsibility.

## Verification and rollback

- Render and syntax-check the changed HelmRelease, then run the repository's
  Kubernetes validation available in the worktree.
- Review the focused diff for only the approved removals.
- After Flux applies the PR, confirm Jellyfin is Ready and inspect logs for
  VA-API detection and playback failures.
- Revert the PR if representative media loses tracks, playback fails, or
  VA-API is no longer available.
