# Drives

Inventory, write-cache policy and health of every drive behind the cluster, including the TrueNAS
host (`192.168.0.27`) that runs control-1 as a VM. Snapshot taken 2026-09-24.

## Write cache: what `write back` and `write through` mean

`/sys/block/<dev>/queue/write_cache` is the kernel's view of the drive, and it only decides whether
the kernel sends flushes. It does not change how the drive's controller caches data.

- `write back`: the drive advertises a volatile cache, so every `fsync`/`fdatasync` also sends a
  flush that forces the drive's cached writes to non-volatile media. Correct for drives
  **without** power-loss protection (PLP).
- `write through`: the drive advertises no volatile cache, so the kernel skips the flush. Correct
  for drives **with** PLP, whose cache is already durable. A PLP drive cannot be forced to flush
  anyway, it ignores the request.

etcd and Ceph issue sync writes constantly, so a consumer NVMe that benchmarks fast still pays a
flush on every one. That is why osd.0 (Samsung 980 PRO) is the slowest OSD.

### Policy

- PLP drives must read `write through`.
- A PLP **SATA** SSD that reads `write back`: turn the volatile cache off in firmware (persists
  across reboots and hosts):
  `smartctl -s wcache-sct,off,p /dev/sdX`
- A PLP **SAS** SSD that reads `write back`:
  `sdparm --clear=WCE --save /dev/sdX`
- NVMe has no persistent equivalent: the kernel follows the `vwc` bit the drive reports
  (`nvme id-ctrl /dev/nvmeX | grep -w vwc`, `0` = no volatile cache).
- Never disable the cache on HDDs or on any drive without PLP: that trades durability for nothing
  and destroys write throughput.

## Inventory

| host | drive | role | PLP | write_cache |
|---|---|---|---|---|
| control-2 | Samsung 980 PRO 1TB | Ceph osd.0 | no | write back |
| control-2 | Micron 7450 PRO 480GB | Talos system disk (etcd) | yes | write through |
| control-3 | Micron 7450 PRO 960GB | Ceph osd.1 | yes | write through |
| control-3 | Micron 7450 PRO 480GB | Talos system disk (etcd) | yes | write through |
| control-1 | virtio `vda` (`5yH5nnaI`) | Talos system disk (etcd) | via host | write back |
| control-1 | virtio `vdb` (`ZF6YrnfZ`) | Ceph osd.3 | via host | write back |
| TrueNAS | Samsung PM983 1.92TB (`nvme0`) | `SSD_Pool` (single disk) | yes | write through, `vwc 0` |
| TrueNAS | Intel Optane M10 32GB (`nvme1`) | `boot-pool` | no | write through, `vwc 0` |
| TrueNAS | 2x Seagate Exos 8TB ST8000NM000A (`sda`, `sdc`) | `TanguilleServer` mirror | no | write back (keep) |
| TrueNAS | 3x Toshiba MG08ACA16TE 16TB (`sdb`, `sdd`, `sde`) | `BIGHDDZ1` raidz1 | no | write back (keep) |

## control-1 disk chain

control-1's virtio disks report `write back`, which is correct: every layer passes the guest's
flushes down to a PLP drive.

| layer | setting |
|---|---|
| guest | virtio-blk `write-cache=on`, sends flushes |
| QEMU | `cache.direct=true`, `no-flush=false`, `aio=io_uring` |
| zvol | `SSD_Pool/vm/TALOS.block` (501G, 16K) and `SSD_Pool/vm/ceph/osd` (800G, 4K, no compression), `sync=standard` |
| pool | `SSD_Pool`, single PM983, ZIL in-pool (no SLOG) |

Keep the virtio disks on `write-cache=on`. The guest's flushes are what make ZFS commit through
the ZIL. With the cache off, QEMU would have to make every write synchronous.

`SSD_Pool` has no redundancy. osd.3 is covered by Ceph replication, and `haos` and
`k8s_management` have copies under `TanguilleServer/VMBackups`. `TALOS.block` has no copy: it is
rebuildable from the Talos config, and etcd survives on the other two members.

## Health (2026-09-24)

| drive | wear used | written | power-on | notes |
|---|---|---|---|---|
| 980 PRO (osd.0) | 67% | 375 TB | 31,087 h | +0.75%/month (64% on 05-02 to 67% on 08-30), about 3.5 years to rated endurance; 186 unsafe shutdowns |
| 7450 480GB, control-2 | 2% | 68 TB | 10,968 h | 28 h above warning temperature |
| 7450 960GB, control-3 | 0% | 39 TB | 6,856 h | 74 h above warning, 15 min above critical |
| 7450 480GB, control-3 | 2% | 81 TB | 10,905 h | 207 h above warning, 236 min above critical (known airflow issue) |
| PM983 | 5% | 504 TB | 42,894 h | |
| Optane M10 | 2% | 7.5 TB | 19,223 h | |
| Exos `sdc` | | | 26,201 h | 40 reallocated sectors, 0 pending: watch for growth |
| other HDDs | | | 21,958 to 35,937 h | 0 reallocated, 0 pending, 0 errors |

All drives pass SMART with 0 media errors. The warning and critical temperature times are
lifetime counters, not current state.

Replacing the 980 PRO with a PLP drive fixes both its wear and osd.0's write latency.

## Commands

Cluster nodes have no shell. Run smartctl inside the smartctl-exporter pod on each node
(read-only):

```sh
kubectl -n observability get pods -o wide | grep smartctl-exporter
kubectl -n observability exec <pod> -- smartctl -x /dev/nvme0
talosctl -n 192.168.0.12 read /sys/block/nvme0n1/queue/write_cache
```

smartctl 7.4 does not print the NVMe `vwc` bit; the kernel's `write_cache` is derived from it.
smartctl cannot read control-1's virtio disks; inspect them from the host.

TrueNAS (`ssh root@192.168.0.27`):

```sh
for d in nvme0n1 nvme1n1; do echo "$d $(cat /sys/block/$d/queue/write_cache)"; done
nvme id-ctrl /dev/nvme0 | grep -w vwc
for d in /dev/sd?; do smartctl -g wcache -g wcache-sct "$d"; done
zpool status; zfs list -t volume -o name,volsize,sync,volblocksize,compression
ls -l /dev/disk/by-partuuid/   # map pool vdevs to devices
```
