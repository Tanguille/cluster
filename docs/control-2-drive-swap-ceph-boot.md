# control-2: Swap Ceph Drive and Boot Drive

You swapped out the Ceph drive on control-2. Goal: **new drive = boot**, **old boot drive = Ceph OSD**.

Current config (before swap):

- **Talos:** `installDisk: "/dev/nvme0n1"` (current boot).
- **Rook-Ceph:** control-2 uses `/dev/disk/by-id/nvme-AirDisk_512GB_SSD_QEN475W011018P1129` (old Ceph drive, now removed).
- **Ceph:** osd.0 (on control-2) is down; host has `noout` set.

---

## 1. Get disk identities on control-2

You must know which block device is the **new** drive (future boot) and which is the **old boot** drive (future Ceph). By-id is stable; device names (nvme0n1 vs nvme1n1) depend on slot/order.

If control-2 is up (even with Ceph down), from your management host:

```bash
talosctl get disks -n 192.168.0.12
```

The disk with **READ ONLY: true** is the system (boot) disk. The other NVMe is available for Ceph.

Or from a shell on control-2 (Talos or debug): `ls -la /dev/disk/by-id/`. Note:

- **New drive** (replacement Ceph drive) → use for Talos `installDisk` (e.g. `/dev/nvme1n1` or by-id).
- **Old boot drive** (e.g. Samsung 980 Pro 1TB) → use its **by-id** for Rook (e.g. `nvme-Samsung_SSD_980_PRO_...`).

---

## 2. Remove the dead OSD (osd.0) from Ceph

`noout` is already set on control-2, so Ceph will not rebalance away from the down OSD. Remove the OSD from the cluster and from Kubernetes.

From a machine with `kubectl` and cluster access:

```bash
# Ceph toolbox (Rook)
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- bash
# or: kubectl rook-ceph ceph ...
```

In the toolbox (or via `kubectl rook-ceph ceph ...`):

```bash
# Mark OSD out (if not already)
ceph osd out 0

# Remove from CRUSH and cluster
ceph osd crush remove osd.0
ceph auth del osd.0
ceph osd rm 0
```

Then remove the Rook OSD deployment so the operator does not try to reuse it:

```bash
kubectl -n rook-ceph get deploy -l ceph-osd-id=0
# Then delete it (name is typically rook-ceph-osd-0 or similar)
kubectl -n rook-ceph delete deployment -l ceph-osd-id=0
```

Leave the toolbox. Ceph will stay degraded until the new OSD is created; that is expected.

---

## 3. Update GitOps: Rook and Talos

**Rook (new Ceph device = old boot drive):**

Edit `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml`. Under `storage.nodes` for `control-2`, set `devices` to the **old boot drive** by-id (the one that will become the new OSD):

```yaml
          - name: control-2
            devices:
              - name: /dev/disk/by-id/<OLD_BOOT_DRIVE_BY_ID>
            config:
              osdsPerDevice: "1"
```

**Talos (new boot drive):**

Edit `talos/talconfig.yaml`. For the control-2 node, set `installDisk` to the **new** drive. Prefer a stable identifier:

- If you use a path: e.g. `"/dev/nvme1n1"` (whichever is the new drive).
- If your Talos/talhelper supports it, you can use by-id for stability (e.g. `"/dev/disk/by-id/<NEW_DRIVE_BY_ID>"` — confirm in Talos docs).

Example:

```yaml
  - hostname: "control-2"
    ipAddress: "192.168.0.12"
    installDisk: "/dev/nvme1n1"   # or by-id of the new drive
    # ... rest unchanged
```

Commit and push (or apply via your GitOps flow). Do **not** reinstall Talos yet.

---

## 4. Apply Rook changes and let the new OSD be created (optional order)

Reconcile Flux so the updated Rook HelmRelease is applied:

```bash
task reconcile
# or: flux reconcile kustomization ...
```

Rook will see control-2 with the new device (old boot drive) and will create a new OSD. Wait until the new OSD is up and PGs are recovering. You can keep `noout` until after Talos reinstall if you prefer.

---

## 5. Install Talos to the new drive from the running node (upgrade)

Talos applies `machine.install` (including `disk`) during **install or upgrade**. From a running node: apply config with the new `installDisk`, then run **upgrade**; the upgrade uses the installer image and writes to the disk in config (the Micron).

1. **Regenerate config** so control-2 has `machine.install.disk: /dev/nvme1n1`:

   ```bash
   task talos:generate-config
   ```

2. **Apply config** so the node has the new install disk in its machine config:

   ```bash
   task talos:apply-node IP=192.168.0.12
   ```

3. **Run upgrade** so Talos is written to the disk in config (nvme1n1 = Micron). The node will reboot:

   ```bash
   task talos:upgrade-node IP=192.168.0.12
   ```

4. **Set BIOS** to boot from the **Micron** (nvme1n1). After the upgrade reboot, the node may still boot from the Samsung; change the boot order so the Micron is first, then reboot again. After that, control-2 runs from the Micron and the Samsung is free for Rook.

---

## 6. Clear noout and rebalance

When control-2 is back and the new OSD is up, clear the noout flag (it was set on **host** control-2) so Ceph can rebalance:

```bash
kubectl rook-ceph ceph osd unset-group noout control-2
```

If your plugin does not support `unset-group`, use the toolbox and run `ceph osd unset-group noout control-2` inside it. Then check health and rebalance:

```bash
kubectl rook-ceph ceph health detail
kubectl rook-ceph ceph -s
```

---

## 7. If the new OSD is not created on control-2

**1. Check the OSD prepare job logs** (replace the pod name with the one for control-2):

```bash
kubectl -n rook-ceph get pod -l app=rook-ceph-osd-prepare
kubectl -n rook-ceph logs rook-ceph-osd-prepare-control-2-<suffix> provision
```

Common causes:

- **Device has partitions or filesystem** – Rook only uses raw devices. The Samsung was the old boot disk, so it still has Talos partitions. You must **zap** (wipe) the disk on control-2, then let Rook create the OSD.
- **Device path wrong** – The by-id we use might not exist on the node (e.g. Talos uses a different format). Get the real path from a pod on control-2: `ls /dev/disk/by-id/` and update the HelmRelease.

**2. Zap the Samsung disk on control-2** (only if prepare logs say the device was skipped because it is in use or has partitions). On control-2 the Samsung is **nvme0n1** (1 TB). Run a one-shot privileged pod that installs `gdisk` and `util-linux`, then wipes the disk. Use `--force` if the device is busy (e.g. Rook or a mount had it open).

```bash
# Optional: stop Rook from holding the device, then zap
kubectl -n rook-ceph scale deployment rook-ceph-operator --replicas=0
kubectl -n rook-ceph delete pod -l app=rook-ceph-osd-prepare

kubectl run zap-control-2 --restart=Never \
  --image=debian:trixie-slim \
  --overrides='{"spec":{"nodeName":"control-2","hostPID":true,"hostNetwork":true,"containers":[{"name":"zap","image":"debian:bookworm-slim","command":["bash","-c","apt-get update -qq && apt-get install -y -qq gdisk util-linux parted && wipefs -a --force /dev/nvme0n1 && sgdisk --zap-all /dev/nvme0n1 && partprobe /dev/nvme0n1 && echo Zapped nvme0n1"],"securityContext":{"privileged":true},"volumeMounts":[{"name":"dev","mountPath":"/dev"}]}],"volumes":[{"name":"dev","hostPath":{"path":"/dev"}}]}}'

kubectl logs -f zap-control-2
kubectl delete pod zap-control-2
```

Then trigger Rook to re-scan and create the OSD (restart operator and re-run prepare):

```bash
kubectl -n rook-ceph scale deployment rook-ceph-operator --replicas=1
kubectl -n rook-ceph delete pod -l app=rook-ceph-osd-prepare
kubectl -n rook-ceph delete pod -l app=rook-ceph-operator
```

Wait for the new prepare job and OSD to come up, then check:

```bash
kubectl rook-ceph ceph osd tree
```

**If prepare still sees partitions** (logs show nvme0n1p1–p4): the host kernel may not have re-read the partition table after the zap. Run `partprobe /dev/nvme0n1` on the node so the kernel drops the old partition devices, then trigger prepare again. The zap command above includes `partprobe`; if you zapped without it, run a one-shot pod that only runs partprobe:

```bash
kubectl run partprobe-control-2 --restart=Never --image=debian:bookworm-slim \
  --overrides='{"spec":{"nodeName":"control-2","hostPID":true,"hostNetwork":true,"containers":[{"name":"p","image":"debian:bookworm-slim","command":["bash","-c","apt-get update -qq && apt-get install -y -qq parted && partprobe /dev/nvme0n1 && echo Done"],"securityContext":{"privileged":true},"volumeMounts":[{"name":"dev","mountPath":"/dev"}]}],"volumes":[{"name":"dev","hostPath":{"path":"/dev"}}]}}'
kubectl delete pod partprobe-control-2
kubectl -n rook-ceph delete pod -l app=rook-ceph-osd-prepare
kubectl -n rook-ceph delete pod -l app=rook-ceph-operator
```

---

## 8. Summary

| Step | Action |
|------|--------|
| 1 | Identify new drive (boot) and old boot drive (Ceph) by-id/path on control-2 |
| 2 | Remove osd.0 from Ceph (out, crush, auth, osd rm) and delete its K8s deployment |
| 3 | Update Rook device for control-2 → old boot by-id; Talos installDisk for control-2 → new drive |
| 4 | Push/apply GitOps; let Rook create the new OSD on the old boot drive |
| 5 | Apply config, run upgrade (writes to new drive); set BIOS to boot from new drive |
| 6 | Unset noout; verify Ceph health and rebalance |
| 7 | If no OSD on control-2: check prepare logs; zap Samsung if it has partitions; restart prepare/operator |

**Important:** Use the **old boot drive** by-id in Rook (the disk that had the OS before the reinstall). Use the **new** drive path or by-id in Talos `installDisk`. Do not mix them up.
