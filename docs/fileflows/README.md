# FileFlows encoding and size safety

These files preserve the Main flow configuration applied on 2026-10-18
(revision 23). They are a recovery/reference copy; Flux does not install them.

## Behavior

- Rejected encodes select **Original File**, so FileFlows reports the retained
  original's size rather than the discarded temporary output's size.
- A final **File Size Compare**, after Dolby Vision repair, allows only smaller
  outputs to reach **Replace Original**. Equal/larger outputs and duration
  mismatches retain the original.
- Three samples, up to 20 seconds each at 15%, 50%, and 85%, estimate QP22
  savings, reserving space for original nonvideo streams and container overhead.
  The predicted saving must be at least 10%.
- For SDR, if QP22 misses that target, test VBR at 90% and 75% of source video
  bitrate. Accept only candidates meeting the size target with SSIM at least
  equal to QP22 on every sampled scene. HDR/10-bit never enter VBR selection.
- Captions, unsupported sampling settings, and sampling timeouts continue with
  the existing native encoder. Full encoding has no custom speed limit or
  watchdog. Invalid media and explicit size/quality failures retain originals.

A53 caption passthrough caused the observed HEVC VAAPI slowdown in both installed
FFmpeg builds. Captions remain enabled; no quality-preserving speed fix was
verified. Sampling has a ten-minute total budget and per-command timeouts.
SSIM and size estimates cannot guarantee quality or savings across a whole file;
the final actual-size gate still applies. Historical statistics are unchanged.

## Configuration and recovery

- `main-size-safety.json`: native Main flow export. Compare with the current
  flow before importing, preserving library assignments and later edits.
- `preflight.js`: native FileFlows Script, uid
  `0a6e7dd7-6e06-44cc-985f-31b8781e3c7b`, revision 2.
- `preflight.py`: helper installed at `/app/Data/fileflows-preflight.py` on the
  configuration PVC. It samples into the runner's temporary directory and
  cleans up automatically. Update the native Script and helper together.

The existing Dolby Vision Script (`f5eebc75-e22d-4181-af02-5e7263e68acd`)
must remain installed. Native export booleans are not interchangeable with the
database's JSON representation; use native import rather than copying export
JSON into SQLite. The changes were applied with FileFlows stopped and backed up.

Backups on the configuration PVC:

| Change | Directory under `/app/Data/Backups/` |
| --- | --- |
| Original size guards | `size-safety-20261018T112728Z/` |
| Sampling and adaptive bitrate | `efficiency-20261018T120038Z/` |
| Allow slow encoding, remove caption skips | `allow-slow-20261018T124149Z/` |

The latest backup includes the previous database, helper, export, and retry
manifest. Revision 23 requeued 31 unchanged originals skipped by preflight,
excluding six known size/quality rejections. Every retried source existed at its
original byte size and had never executed Replace Original.

## Verification

```bash
python3 -m unittest discover -s docs/fileflows -p 'test_*.py' -v
node docs/fileflows/test_preflight.mjs
```

Local checks cover size overhead, per-scene quality, caption/timeout fallback,
error handling, and native routing. A disposable native integration encode
verified VBR expansion without residual QP22 parameters. Real calibration
rejected lower-scoring VBR samples even when smaller. Worker configuration
refreshed at 14:42:30 CEST; a previously skipped captioned Top Gear episode
started native `hevc_vaapi -qp 22 -preset veryslow` encoding at 14:43:00.
A complete slow encode through final replacement and HDR end-to-end behavior
remain unverified.
