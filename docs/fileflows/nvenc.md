# FileFlows on NVENC (RTX 2070)

FileFlows encodes HEVC with `hevc_nvenc` on control-1's RTX 2070 instead of `hevc_vaapi` on the
R9700, which takes the transcode VRAM off the card vLLM shares with Jellyfin. The pod gets
`runtimeClassName: nvidia`, `nvidia.com/gpu: 1` and `NVIDIA_DRIVER_CAPABILITIES:
video,compute,utility`. It keeps `squat.ai/dri`, so a rollback is a flow edit only.

## Measured (2026-11-15)

Throwaway pods on control-1 with the jellyfin-ffmpeg 7.1.4 build the FileFlows DockerMod
installs, 30 s clips copied from the library, own VRAM from `nvidia-smi --query-compute-apps`,
VMAF from libvmaf against the source (frames aligned by index). No `nvidia_drm`, `cdi.enabled:
false`. The flow's encode node at Quality *q* and Speed 1 emits
`hevc_nvenc -rc constqp -qp <QP> -preset p7 -spatial-aq 1` (Quality 6 = QP 22, 9 = QP 18).

| Source | Encode | Speed | Own VRAM | Size vs source | VMAF mean / p5 |
| --- | --- | --- | --- | --- | --- |
| 1080p H.264 SDR | VAAPI QP 22 (today) | 21.2x | ~0.1 GiB on the R9700 | 112 % | 98.4 / 95.8 |
| 1080p H.264 SDR | NVENC QP 22 | 5.7x | 328 MiB | 54 % | 95.5 / 92.1 |
| 1080p H.264 SDR | NVENC QP 18 | 5.9x | 328 MiB | 113 % | 98.2 / 95.6 |
| 1080p HEVC SDR | VAAPI QP 22 (today) | 19.0x | ~0.1 GiB on the R9700 | 95 % | 99.98 / 100 |
| 1080p HEVC SDR | NVENC QP 18 | 5.9x | 326 MiB | 76 % | 99.7 / 97.9 |
| 1080p VP9 | VAAPI QP 22 (today) | 18.2x | ~0.1 GiB on the R9700 | 142 % | 99.3 / 96.2 |
| 1080p VP9 | NVENC QP 22 | 5.0x | 380 MiB | 98 % | 97.0 / 92.8 |
| 4K HDR10 HEVC 10-bit | NVENC QP 22 | 1.6x | 1508 MiB | 63 % | 92.2 / 85.6 |
| 4K HDR10 HEVC 10-bit | NVENC QP 18 | 1.6x | 1508 MiB | 140 % | 95.3 / 90.3 |

- At a matched bitrate NVENC p7 scores within 0.2 VMAF of VAAPI (98.3 vs 98.4, 99.94 vs 99.98,
  99.1 vs 99.3) at 1-14 % more bits. QP 18 keeps today's quality bar; QP 22 is a policy change
  to about half the size at VMAF 95.
- 4K: `-preset p5` runs 2.5x at the same size and VRAM. Software decode drops VRAM to 766 MiB
  but uses 3.7 cores. VMAF is not calibrated for PQ, so read the 4K rows as relative only.
  There is no 4K VAAPI row: that run took over 0.5 GiB of R9700 VRAM and was stopped.
- Power 49-51 W while encoding (idle 7 W), CPU 0.07-0.18 cores (AV1 source 1.1 cores).
- AV1 has no NVDEC on Turing (`av1_cuvid ... not supported`); FileFlows has to fall back to
  software decode for those. The library had no AV1 or VP9 sources in a sample of 400.
- 12 concurrent NVENC sessions opened without error, so the GeForce session cap is not the limit.
- `tonemap_cuda` and `scale_cuda` work. `tonemap_opencl` and Vulkan/libplacebo do not (no NVIDIA
  OpenCL or Vulkan ICD in the container); the flow uses neither.
- `-sei:v:0 hdr` is a `hevc_vaapi` option; `hevc_nvenc` ignores it with a warning. Its
  equivalent is `-a53cc:v:0 0`.
- A VAAPI AV1 decode on the R9700 hung `vcn_unified_0` and triggered a VCN ring reset
  (vLLM kept serving).

## Budget

One runner. A 4K 10-bit session holds 1.5 GiB; the three llama.cpp services take 5412 of 7786
MiB, so one 4K encode leaves about 0.85 GiB free, above `NvidiaVramLow` (512 MiB). Two 4K
runners do not fit. The UPS load-shed rule does not see the 2070, which adds about 45 W
while encoding.

## Owner steps

1. Merge and reconcile. The flow still says VAAPI and keeps working, because `/dev/dri` stays.
2. Check the card: `kubectl -n media exec deploy/fileflows -c app -- nvidia-smi -L`.
3. In the FileFlows UI, flow **Main**:
   1. **HEVC** (Video Encode Simple): Encoder `VAAPI` -> `NVIDIA`, Quality `6` -> `9`, Speed `1`.
   2. **Drop A53 caption SEI** (Custom Parameters): `-sei:v:0 hdr` -> `-a53cc:v:0 0`.
   3. Leave Hardware Decoding on `auto` in both executors and Flow Runners at 1.
4. Reprocess one file and check its log for `-hwaccel cuda` and
   `hevc_nvenc -rc constqp -qp 18 -preset p7 -spatial-aq 1`.

The size preflight samples with `hevc_vaapi` only. With NVENC it logs `Unsupported sampling
profile` and goes to the full encode, and File Size Compare still rejects larger outputs. To
sample with NVENC, edit `/app/Data/fileflows-preflight.py` on the PVC.

## Rollback

Undo step 3: Encoder `VAAPI`, Quality `6`, parameters `-sei:v:0 hdr`. Reverting the commit is
optional because `squat.ai/dri` is still requested.
