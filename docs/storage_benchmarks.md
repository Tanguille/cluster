# Storage Performance Benchmarks

Performance benchmarks for the Rook Ceph storage classes. The Mayastor and OpenEBS ZFS runs they were compared against are in git at `db102e988`; neither is deployed any more.

## Test Methodology

This document contains two sets of benchmarks:

### fio Benchmarks (10GB Test Size)

All benchmarks were performed using `fio` (Flexible I/O Tester) with the following test scenarios:

1. **Sequential Write**: 1MB blocks, 4 parallel jobs, 30 second runtime
2. **Sequential Read**: 1MB blocks, 4 parallel jobs, 30 second runtime
3. **Random Write**: 4KB blocks, 16 parallel jobs, 30 second runtime
4. **Random Read**: 4KB blocks, 16 parallel jobs, 30 second runtime

All tests use direct I/O (`direct=1`) to bypass page cache and measure true storage performance. Test file size: 10GB.

### kbench Benchmarks (30GB Test Size)

The same storage classes were also tested using `kbench` (Longhorn's Kubernetes storage benchmarking tool) with a 30GB test size. kbench uses standardized fio configurations and provides a consistent testing methodology. kbench provides average latency only (not percentiles).

Keep in mind these are not scientific benchmarks, since the cluster is not dedicated to the benchmarks there were random other workloads running on the cluster. This is just a rough idea of the performance of the storage solutions and it's intrinsic characteristics to make a decision on what to use when.

## Test Environment

- **Cluster**: 3-node Kubernetes cluster (control-1, control-2, control-3)
- **Kubernetes**: Talos Linux
- **Storage Backend**: Rook Ceph with RBD (block storage) and CephFS
- **Volume Size**:
  - fio tests: 20Gi volumes with 10GB test files
  - kbench tests: 33Gi volumes with 30GB test files
- **Date**: December 13-14, 2025

For hardware specifications, see the [README](../README.md).

## kbench Benchmark Results (30GB Test Size)

**Note**: These results were obtained using `kbench` (Longhorn's Kubernetes storage benchmarking tool).

### Rook Ceph (RBD - Block Storage) (kbench)

**StorageClass**: `ceph-block`
**Test Size**: 30GB
**Test Node**: control-1

#### Sequential Write

- **Throughput**: 168 MiB/s (176 MB/s)
- **IOPS**: 3,848
- **Average Latency**: 8.645 ms

#### Sequential Read

- **Throughput**: 650 MiB/s (681 MB/s)
- **IOPS**: 7,395
- **Average Latency**: 0.275 ms

#### Random Write (4KB blocks)

- **Throughput**: 183 MiB/s (192 MB/s)
- **IOPS**: 3,699
- **Average Latency**: 9.284 ms

#### Random Read (4KB blocks)

- **Throughput**: 760 MiB/s (797 MB/s)
- **IOPS**: 27,464
- **Average Latency**: 0.316 ms

### Rook Ceph (CephFS - Filesystem Storage) (kbench)

**StorageClass**: `ceph-filesystem`
**Test Size**: 30GB
**Test Node**: control-1

#### Sequential Write

- **Throughput**: 166 MiB/s (174 MB/s)
- **IOPS**: 3,844
- **Average Latency**: 8.630 ms

#### Sequential Read

- **Throughput**: 820 MiB/s (860 MB/s)
- **IOPS**: 7,654
- **Average Latency**: 0.275 ms

#### Random Write (4KB blocks)

- **Throughput**: 178 MiB/s (186 MB/s)
- **IOPS**: 3,650
- **Average Latency**: 9.274 ms

#### Random Read (4KB blocks)

- **Throughput**: 761 MiB/s (798 MB/s)
- **IOPS**: 29,208
- **Average Latency**: 0.311 ms

---

## kbench Performance Comparison

| Metric                       | Ceph RBD  | CephFS    |
|------------------------------|-----------|-----------|
| **Sequential Write**         | 168 MiB/s | 166 MiB/s |
| **Sequential Read**          | 650 MiB/s | 820 MiB/s |
| **Random Write IOPS**        | 3,699     | 3,650     |
| **Random Read IOPS**         | 27,464    | 29,208    |
| **Sequential Write Latency** | 8.645 ms  | 8.630 ms  |
| **Sequential Read Latency**  | 0.275 ms  | 0.275 ms  |
| **Random Write Latency**     | 9.284 ms  | 9.274 ms  |
| **Random Read Latency**      | 0.316 ms  | 0.311 ms  |

### kbench Key Findings

- RBD and CephFS are within 2% of each other on writes and latency.
- CephFS reads faster: 820 vs 650 MiB/s sequential (1.26x) and 6% higher random read IOPS.
- CephFS supports ReadWriteMany, so multiple pods can mount it simultaneously.

## FIO

### FIO Commands Used

All benchmarks use the same fio commands with the following test scenarios:

**Sequential Write Test (1MB blocks, 4 jobs):**

```bash
fio --name=seq-write --filename=/volume/test-seq-write --size=10G --rw=write --bs=1M --iodepth=16 --numjobs=4 --direct=1 --sync=0 --runtime=30 --time_based --group_reporting
```

**Sequential Read Test (1MB blocks, 4 jobs):**

```bash
fio --name=seq-read --filename=/volume/test-seq-write --size=10G --rw=read --bs=1M --iodepth=16 --numjobs=4 --direct=1 --sync=0 --runtime=30 --time_based --group_reporting
```

**Random Write Test (4KB blocks, 16 jobs):**

```bash
fio --name=rand-write --filename=/volume/test-rand-write --size=10G --rw=randwrite --bs=4k --iodepth=32 --numjobs=16 --direct=1 --sync=0 --runtime=30 --time_based --group_reporting
```

**Random Read Test (4KB blocks, 16 jobs):**

```bash
fio --name=rand-read --filename=/volume/test-rand-write --size=10G --rw=randread --bs=4k --iodepth=32 --numjobs=16 --direct=1 --sync=0 --runtime=30 --time_based --group_reporting
```

## Rook Ceph (RBD - Block Storage)

**StorageClass**: `ceph-block`
**Protocol**: Ceph RBD (RADOS Block Device)
**Replication**: 3 replicas when measured; pools have been size 2 / min_size 1 since 2025-12-16
**Test Node**: control-1

### Sequential Write (1MB blocks, 4 jobs)

- **Throughput**: 94.6 MiB/s (99.2 MB/s)
- **IOPS**: 94
- **Average Latency**: 42.24 ms
- **50th Percentile Latency**: 31 ms
- **99th Percentile Latency**: 140 ms

### Sequential Read (1MB blocks, 4 jobs)

- **Throughput**: 6,906 MiB/s (7,242 MB/s) ⚠️ *Likely cached*
- **IOPS**: 6,906 ⚠️ *Likely cached*
- **Average Latency**: 0.58 ms
- **50th Percentile Latency**: 0.18 ms
- **99th Percentile Latency**: 7.18 ms
- **Note**: Read 202GiB from a 10GB file, indicating cache hits

### Random Write (4KB blocks, 16 jobs)

- **Throughput**: 3.37 MiB/s (3.53 MB/s)
- **IOPS**: 862
- **Average Latency**: 18.50 ms
- **50th Percentile Latency**: 15 ms
- **99th Percentile Latency**: 36 ms

### Random Read (4KB blocks, 16 jobs)

- **Throughput**: 6,537 MiB/s (6,855 MB/s) ⚠️ *Likely cached*
- **IOPS**: 1,674,000 (1.67M IOPS) ⚠️ *Likely cached*
- **Average Latency**: 0.009 ms (8.9 usec)
- **50th Percentile Latency**: 0.0019 ms (1.9 usec)
- **99th Percentile Latency**: 0.212 ms (212 usec)
- **Note**: Read 192GiB from a 10GB file, indicating cache hits

---

## Rook Ceph (CephFS - Filesystem Storage)

**StorageClass**: `ceph-filesystem`
**Protocol**: CephFS (Ceph Filesystem)
**Replication**: 3 replicas when measured; pools have been size 2 / min_size 1 since 2025-12-16
**Access Mode**: ReadWriteMany (supports multiple pods)
**Test Node**: control-1

### Sequential Write (1MB blocks, 4 jobs)

- **Throughput**: 127 MiB/s (133 MB/s)
- **IOPS**: 126
- **Average Latency**: 31.54 ms
- **50th Percentile Latency**: 30 ms
- **99th Percentile Latency**: 89 ms

### Sequential Read (1MB blocks, 4 jobs)

- **Throughput**: 867 MiB/s (909 MB/s)
- **IOPS**: 867
- **Average Latency**: 4.61 ms
- **50th Percentile Latency**: 5 ms
- **99th Percentile Latency**: 10 ms

### Random Write (4KB blocks, 16 jobs)

- **Throughput**: 3.72 MiB/s (3.81 MB/s)
- **IOPS**: 953
- **Average Latency**: 16.78 ms
- **50th Percentile Latency**: 14 ms
- **99th Percentile Latency**: 26 ms

### Random Read (4KB blocks, 16 jobs)

- **Throughput**: 86.5 MiB/s (90.8 MB/s)
- **IOPS**: 22,200
- **Average Latency**: 0.70 ms
- **50th Percentile Latency**: 0.60 ms
- **99th Percentile Latency**: 2.28 ms

---

## Performance Comparison

| Metric                             | Ceph RBD       | CephFS    |
|------------------------------------|----------------|-----------|
| **Sequential Write**               | 94.6 MiB/s     | 127 MiB/s |
| **Sequential Read**                | 6,906 MiB/s ⚠️ | 867 MiB/s |
| **Random Write IOPS**              | 862            | 953       |
| **Random Read IOPS**               | 1,674,000 ⚠️   | 22,200    |
| **Random Write Latency (p50)**     | 15 ms          | 14 ms     |
| **Random Read Latency (p50)**      | 0.0019 ms ⚠️   | 0.60 ms   |
| **Sequential Write Latency (p50)** | 31 ms          | 30 ms     |
| **Sequential Read Latency (p50)**  | 0.18 ms ⚠️     | 5 ms      |

---

## Analysis and Recommendations

### Ceph Advantages

**RBD (Block Storage):**

- **Exceptional Sequential Read Performance**: 6,906 MiB/s ⚠️ *Likely cached*
- **Ultra-High Random Read IOPS**: 1,674,000 IOPS ⚠️ *Likely cached*
- **Ultra-Low Random Read Latency**: 0.0019 ms
- **⚠️ Cache Warning**: Read performance numbers are likely inflated due to Ceph's internal cache serving data after write tests
- **High Availability**: Built-in replication with automatic failover (measured at 3 replicas, now size 2 / min_size 1)
- **Enterprise-Grade**: Mature, production-tested distributed storage system
- **Scalability**: Can scale to thousands of nodes and petabytes of storage
- **Multiple Access Methods**: Supports both block (RBD) and filesystem (CephFS) interfaces

**Considerations:**

- **Sequential Write Performance**: Lower than CephFS (94.6 vs 127 MiB/s)
- **Random Write Performance**: Lower IOPS than CephFS (862 vs 953) and higher p50 latency (15 ms vs 14 ms)
- **Resource Usage**: Higher CPU and memory overhead compared to local storage solutions

**Choose Ceph RBD when:**

- **Ultra-high random read performance** - 1.67M IOPS ⚠️ *Likely cached*
- **Ultra-low random read latency** - 0.0019 ms
- **Exceptional sequential read performance** - 6,906 MiB/s ⚠️ *Likely cached*
- **Note**: Read performance numbers are likely inflated due to Ceph's cache. Actual disk performance would be lower.
- **High availability and scalability** - enterprise-grade distributed storage with automatic replication
- **Multi-protocol support** - need both block (RBD) and filesystem (CephFS) access
- **Large-scale deployments** - can scale to thousands of nodes
- **Write performance is not critical** - acceptable trade-off for read-heavy workloads

### CephFS Advantages

**CephFS (Filesystem Storage):**

- **ReadWriteMany Support**: Can be mounted by multiple pods simultaneously (unlike block storage)
- **Better Sequential Write than RBD**: 127 MiB/s vs 94.6 MiB/s (34% faster)
- **Better Random Write than RBD**: 953 IOPS vs 862 IOPS (11% higher)
- **Lower Random Write Latency than RBD**: 14 ms vs 15 ms p50
- **Shared Filesystem**: Traditional POSIX filesystem semantics for shared access
- **High Availability**: Built-in replication with automatic failover (measured at 3 replicas, now size 2 / min_size 1)
- **Enterprise-Grade**: Mature, production-tested distributed filesystem

**Considerations:**

- **Sequential Read Performance**: Significantly lower than RBD (867 vs 6,906 MiB/s) - 8x slower
- **Random Read Performance**: Much lower than RBD (22,200 vs 1,674,000 IOPS) - 75x lower
- **Random Read Latency**: Higher than RBD (0.60 ms vs 0.0019 ms p50) - 316x higher
- **Sequential Read Latency**: Higher than RBD (5 ms vs 0.18 ms p50) - 28x higher
- **Resource Usage**: Higher CPU and memory overhead compared to local storage solutions

**Choose CephFS when:**

- **Shared filesystem access** - need ReadWriteMany (multiple pods accessing same volume)
- **POSIX filesystem semantics** - require traditional filesystem features and compatibility
- **Better write performance than RBD** - 34% faster sequential writes, 11% higher random write IOPS
- **Shared storage for applications** - web servers, content management, shared databases
- **High availability with shared access** - need both HA and multi-pod access
- **Read performance is acceptable** - can accept lower read performance for shared access benefits
- **Write-heavy shared workloads** - better write characteristics than RBD for shared scenarios
