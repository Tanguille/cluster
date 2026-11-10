# Storage Performance Benchmarks

Benchmarks of Rook Ceph, which the cluster runs today, and of OpenEBS Mayastor and OpenEBS ZFS, which were compared in December 2025 and are no longer deployed. These are rough numbers from a live cluster with other workloads running, not a lab result. For hardware, see the [README](../README.md).

## Ceph follow-up, 2026-11-06

Same fio commands, test node (control-1) and storage classes as December 2025. Run-to-run spread was within 3%.

| Test                     | RBD Dec | RBD Nov | Change | CephFS Dec | CephFS Nov | Change |
|--------------------------|---------|---------|--------|------------|------------|--------|
| Sequential write (MiB/s) | 94.6    | 168.3   | +78%   | 127        | 180.4      | +42%   |
| Sequential write p50/p99 (ms) | 31 / 140 | 22.4 / 49.0 | better | 30 / 89 | 21.4 / 41.7 | better |
| Random write (IOPS)      | 862     | 2,241   | +160%  | 953        | 2,453      | +157%  |
| Random write p50/p99 (ms) | 15 / 36 | 6.1 / 35.9 | better | 14 / 26 | 5.9 / 31.6 | p50 better, p99 worse |
| Sequential read (MiB/s)  | invalid | 303     | none   | 867        | 397        | -54%   |
| Sequential read p50/p99 (ms) | invalid | 15.1 / 32.6 | none | 5 / 10 | 12.8 / 20.8 | worse |
| Random read (IOPS)       | invalid | 25,129  | none   | 22,200     | 28,829     | +30%   |
| Random read p50/p99 (ms) | invalid | 0.59 / 1.81 | none | 0.60 / 2.28 | 0.47 / 1.79 | better |

**Writes roughly doubled.** Pools went from size 3 to size 2 on 2025-12-16, so each write now waits for one fewer replica. Ceph, Rook (1.18.8 to 1.21.2), Talos (1.11.5 to 1.14.5) and the kernel (now 7.3.1) all moved too, so the gain cannot be split between them.

**CephFS sequential read halved.** Likely cause, not proven: CSI `readAffinity` was on in December and has been off since 2026-11-02, the fix for Ceph v21 rejecting CephFS writes. With size 3 and read affinity, a client on control-1 always had a local copy. The run has only 4 synchronous jobs, so it is latency bound: 4 x 1 MiB per 10 ms is the measured 400 MiB/s.

**RBD read numbers have no valid December baseline.** December's 6,906 MiB/s and 1.67M IOPS were marked "likely cached", but those commands mostly read unwritten extents, which ext4 answers with zeros without touching Ceph. The same commands today report 2,079 MiB/s and 876,000 IOPS. The Nov column instead reads a file fully written first (CephFS was unaffected: 390 MiB/s and 28,957 IOPS on a written file).

**kbench was not repeated.** The 30 GB RBD fill pushed osd.3 to about 1 s average write latency and raised `BLOCK_DEVICE_DISCARD_QUEUE` (HEALTH_WARN), so a watcher stopped it. It cleared on its own with no OSD restarts. Discard backlog on osd.3 limits how hard this cluster can be benchmarked during normal operation.

Run details: fio 3.16 from `yasker/kbench@sha256:987ec6de`, against December's unrecorded fio version. Volumes were 40Gi, because 20Gi runs out of space on two 10 GiB files plus a written read file. Peak etcd WAL fsync p99 was 0.109 s and peak NVMe temperature 62 °C.

## Method

No `--ioengine` is set, so fio uses `psync` and `--iodepth` has no effect: concurrency is `--numjobs`. All tests use direct I/O.

```bash
fio --name=seq-write --filename=/volume/test-seq-write --size=10G --rw=write --bs=1M --iodepth=16 --numjobs=4 --direct=1 --sync=0 --runtime=30 --time_based --group_reporting
fio --name=seq-read --filename=/volume/test-seq-write --size=10G --rw=read --bs=1M --iodepth=16 --numjobs=4 --direct=1 --sync=0 --runtime=30 --time_based --group_reporting
fio --name=rand-write --filename=/volume/test-rand-write --size=10G --rw=randwrite --bs=4k --iodepth=32 --numjobs=16 --direct=1 --sync=0 --runtime=30 --time_based --group_reporting
fio --name=rand-read --filename=/volume/test-rand-write --size=10G --rw=randread --bs=4k --iodepth=32 --numjobs=16 --direct=1 --sync=0 --runtime=30 --time_based --group_reporting
```

To measure real reads, fill the file first and point the read commands at it:

```bash
fio --name=prefill --filename=/volume/test-filled --size=10G --rw=write --bs=1M --numjobs=1 --direct=1 --refill_buffers --group_reporting
```

`kbench` (Longhorn's Kubernetes storage benchmark) ran with a 30 GB file on 33Gi volumes and reports average latency only.

## December 2025 environment

- 3-node Talos cluster, all tests on control-1, 2025-12-13 and 14. fio used 20Gi volumes.
- **Ceph:** RBD (`ceph-block`) and CephFS (`ceph-filesystem`), size 3 when measured.
- **Mayastor:** NVMe-oF/TCP on control-1's TrueNAS ZVOL (537GB) and AirDisk 512GB SSDs on control-2 and control-3. Default is 2 CPU cores per IO engine; the 1-core runs used `cpuCount: 1` and `coreList: [3]`.
- **ZFS:** LocalPV on pool `speed`, 128KB record size, lz4, no dedup, thin provisioned.

## December 2025 fio results

Throughput in MiB/s, plus IOPS for random tests.

| Backend                  | Seq write | Seq read | Rand write IOPS | Rand read IOPS |
|--------------------------|-----------|----------|-----------------|----------------|
| Mayastor single, 2 cores | 2,158     | 3,573    | 32,000          | 102,000        |
| Mayastor single, 1 core  | 133       | 1,369    | 15,000          | 13,100         |
| Mayastor 3 replicas      | 129       | 2,784    | 5,030           | 265,000        |
| ZFS                      | 132       | 4,711    | 9,237           | 59,300         |
| Ceph RBD                 | 94.6      | 6,906 *  | 862             | 1,674,000 *    |
| CephFS                   | 127       | 867      | 953             | 22,200         |

Latency in ms, as average / p50 / p99.

| Backend                  | Seq write        | Seq read            | Rand write        | Rand read               |
|--------------------------|------------------|---------------------|-------------------|-------------------------|
| Mayastor single, 2 cores | 1.84 / 1.35 / 11.1 | 1.12 / 1.07 / 2.3 | 0.50 / 0.33 / 3.2 | 0.16 / 0.17 / 0.89      |
| Mayastor single, 1 core  | 29.98 / 16 / 255 | 2.92 / 0.16 / 129.5 | 1.06 / 0.44 / 49.5 | 1.22 / 0.60 / 50.1     |
| Mayastor 3 replicas      | 30.9 / 31 / 391  | 1.43 / 0.1 / 12.9   | 3.17 / 0.96 / 32.1 | 0.06 / 0.0026 / 0.85   |
| ZFS                      | 30.2 / 9.9 / 329 | 0.64 / 0.59 / 1.3   | 1.72 / 0.052 / 6.1 | 0.27 / 0.21 / 1.1      |
| Ceph RBD                 | 42.24 / 31 / 140 | 0.58 / 0.18 / 7.18 * | 18.50 / 15 / 36  | 0.009 / 0.0019 / 0.212 * |
| CephFS                   | 31.54 / 30 / 89  | 4.61 / 5 / 10       | 16.78 / 14 / 26   | 0.70 / 0.60 / 2.28      |

\* Not a Ceph figure: see the follow-up. Mayastor's 3-replica read latencies have the same shape and may share the cause.

Dropping Mayastor from 2 cores to 1 halves its CPU use and costs 16.2x on sequential writes, 2.6x on sequential reads, 2.1x on random write IOPS and 7.8x on random read IOPS. At 1 core it ties ZFS on sequential writes (133 and 132 MiB/s), beats it 1.6x on random write IOPS, and loses 3.4x on sequential reads and 4.5x on random read IOPS.

## December 2025 kbench results

Throughput in MiB/s, IOPS, and average latency in ms.

| Test             | ZFS                   | Ceph RBD              | CephFS                |
|------------------|-----------------------|-----------------------|-----------------------|
| Sequential write | 86 / 19,712 / 0.018   | 168 / 3,848 / 8.645   | 166 / 3,844 / 8.630   |
| Sequential read  | 561 / 6,960 / 0.137   | 650 / 7,395 / 0.275   | 820 / 7,654 / 0.275   |
| Random write     | 81 / 1,337 / 0.893    | 183 / 3,699 / 9.284   | 178 / 3,650 / 9.274   |
| Random read      | 500 / 4,889 / 0.203   | 760 / 27,464 / 0.316  | 761 / 29,208 / 0.311  |

ZFS had 1.6x to 480x lower latency; Ceph had 1.95x the sequential write throughput and 2.8x to 6x the IOPS. CephFS led sequential reads at 1.26x RBD and 1.46x ZFS.

## December 2025 takeaways

| Backend             | Strength                                                        | Cost                                                   |
|---------------------|-----------------------------------------------------------------|--------------------------------------------------------|
| Mayastor single     | Fastest writes: 16x ZFS sequential, 3.5x ZFS random IOPS        | No redundancy; needs its CPU cores                     |
| Mayastor 3 replicas | Replicated, with strong sequential reads                        | Sequential writes fall to 129 MiB/s                    |
| ZFS                 | Lowest small-write latency, compression and snapshots           | Local only, no replication                             |
| Ceph RBD            | Replicated, self-healing, scales out                            | Highest write latency; CPU and memory overhead         |
| CephFS              | ReadWriteMany with POSIX semantics, writes close to RBD         | Slower reads than local storage                        |
