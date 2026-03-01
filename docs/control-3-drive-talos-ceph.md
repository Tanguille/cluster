# control-3: 480GB = Talos, 960GB = Rook-Ceph

Goal: **480GB NVMe (nvme1n1)** = Talos boot, **960GB NVMe (nvme0n1)** = Rook-Ceph OSD.

Current Git config (already correct):

- **Talos:** `installDisk: "/dev/disk/by-id/nvme-Micron_7450_MTFDKBA480TFR_241548387F46"` (480GB).
- **Rook-Ceph:** control-3 uses `/dev/disk/by-id/nvme-eui.000000000000000100a075244aa8faf6` (960GB).

---

## If control-3 is already booting from 480GB

Only ensure Flux has applied the Rook HelmRelease so the 960GB is used for Ceph:

```bash
task reconcile
```

Check Ceph OSD tree: `kubectl rook-ceph ceph osd tree` (control-3 should have one OSD on the 960GB).

---

## If control-3 is still booting from 960GB (need to move Talos to 480GB)

1. **Optional:** If control-3 already has a Ceph OSD on the 480GB (wrong disk), remove that OSD first (see control-2 doc: out, crush remove, auth del, osd rm, delete K8s deployment). Set `noout` on the host if you want to avoid rebalance during the swap.

2. **Regenerate Talos config** (so generated YAML has the 480GB disk):

   ```bash
   task talos:generate-config
   ```

3. **Apply config** to the node:

   ```bash
   task talos:apply-node IP=192.168.0.13
   ```

4. **Upgrade node** (writes Talos to the 480GB; node will reboot):

   ```bash
   task talos:upgrade-node IP=192.168.0.13
   ```

5. **Set BIOS** to boot from the **480GB** NVMe (Micron_7450_MTFDKBA480TFR). After the upgrade reboot, the node may still boot from the 960GB; change boot order so the 480GB is first, then reboot again.

6. When control-3 is back, if you set `noout`, clear it:

   ```bash
   kubectl rook-ceph ceph osd unset-group noout control-3
   ```

---

## If OSD prepare finds no devices on control-3 (disk not clean)

Same situation as control-2 §7: Rook only uses **raw** devices. If the 960GB still has an old partition table (e.g. from when it was the Talos boot disk), zap it on control-3, then re-run prepare. See **control-2-drive-swap-ceph-boot.md** §7 for the same pattern.

**1. Scale down operator and clear prepare pods** (so nothing holds the device):

```bash
kubectl -n rook-ceph scale deployment rook-ceph-operator --replicas=0
kubectl -n rook-ceph delete pod -l app=rook-ceph-osd-prepare
```

**2. Zap the 960GB on control-3** (use by-id so we don’t touch the 480GB Talos disk). On control-3 the 960GB is `/dev/nvme0n1`. Easiest: use the manifest (avoids JSON escaping in shell). If you already ran a zap pod before, delete it first so apply can create a fresh one:

```bash
kubectl delete pod zap-control-3 --ignore-not-found
kubectl apply -f docs/zap-control-3-960gb.yaml
kubectl logs -f zap-control-3
kubectl delete pod zap-control-3
```

Manifest: **docs/zap-control-3-960gb.yaml**

**3. Re-run prepare** (scale operator back up, then delete prepare pods and operator pod so jobs are recreated):

```bash
kubectl -n rook-ceph scale deployment rook-ceph-operator --replicas=1
kubectl -n rook-ceph delete pod -l app=rook-ceph-osd-prepare
kubectl -n rook-ceph delete pod -l app=rook-ceph-operator
```

Wait for the new prepare job and OSD to come up, then check:

```bash
kubectl rook-ceph ceph osd tree
```

**If prepare still sees partitions** (logs show nvme0n1p1–p4): run `partprobe` on the 960GB device from a one-shot pod, then delete prepare pods and operator again (same as control-2 §7).

---

## Disk reference (from `talosctl get disks -n 192.168.0.13`)

| Device   | Size   | Model                  | Use      |
|----------|--------|------------------------|----------|
| nvme0n1  | 960 GB | Micron_7450_MTFDKBA960TFR | Rook-Ceph |
| nvme1n1  | 480 GB | Micron_7450_MTFDKBA480TFR | Talos    |
| sda      | 1.0 TB | USB (RTL9210B-CG)      | —        |
