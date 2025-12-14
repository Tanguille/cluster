# Storage Performance Benchmarks

This document contains performance benchmarks comparing OpenEBS Mayastor, OpenEBS ZFS, and Rook Ceph storage solutions.

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

Some storage backends were also tested using `kbench` (Longhorn's Kubernetes storage benchmarking tool) with a 30GB test size. kbench uses standardized fio configurations and provides a consistent testing methodology. kbench provides average latency only (not percentiles).

Keep in mind these are not scientific benchmarks, since the cluster is not dedicated to the benchmarks there were random other workloads running on the cluster. This is just a rough idea of the performance of the storage solutions and it's intrinsic characteristics to make a decision on what to use when.

## Test Environment

- **Cluster**: 3-node Kubernetes cluster (control-1, control-2, control-3)
- **Kubernetes**: Talos Linux
- **Storage Backend**:
  - **Mayastor**: Single-replica volumes on NVMe drives
    - control-1: TrueNAS ZVOL (537GB)
    - control-2: AirDisk 512GB SSD
    - control-3: AirDisk 512GB SSD
  - **ZFS**: OpenEBS ZFS LocalPV on ZFS pool "speed"
  - **Ceph**: Rook Ceph with RBD (block storage) and CephFS
- **Volume Size**:
  - fio tests: 20Gi volumes with 10GB test files
  - kbench tests: 33Gi volumes with 30GB test files
- **Date**: December 13-14, 2025
- **Mayastor CPU Configuration**:
  - **2-core tests**: Default configuration with 2 CPU cores per IO engine
  - **1-core tests**: Optimized configuration with `cpuCount: 1` and `coreList: [3]` to reduce CPU usage by 50%

For hardware specifications, see the [README](../README.md).

## kbench Benchmark Results (30GB Test Size)

**Note**: These results were obtained using `kbench` (Longhorn's Kubernetes storage benchmarking tool).

### OpenEBS ZFS LocalPV (kbench)

**StorageClass**: `openebs-zfs`
**Test Size**: 30GB
**Test Node**: control-1

#### Sequential Write

- **Throughput**: 86 MiB/s (90 MB/s)
- **IOPS**: 19,712
- **Average Latency**: 0.018 ms (18 usec)

#### Sequential Read

- **Throughput**: 561 MiB/s (588 MB/s)
- **IOPS**: 6,960
- **Average Latency**: 0.137 ms

#### Random Write (4KB blocks)

- **Throughput**: 81 MiB/s (85 MB/s)
- **IOPS**: 1,337
- **Average Latency**: 0.893 ms

#### Random Read (4KB blocks)

- **Throughput**: 500 MiB/s (524 MB/s)
- **IOPS**: 4,889
- **Average Latency**: 0.203 ms

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

| Metric | ZFS | Ceph RBD | CephFS | Winner | Difference |
|--------|-----|----------|--------|--------|------------|
| **Sequential Write** | 86 MiB/s | 168 MiB/s | 166 MiB/s | **Ceph RBD** | **1.95x faster** |
| **Sequential Read** | 561 MiB/s | 650 MiB/s | 820 MiB/s | **CephFS** | **1.46x faster** |
| **Random Write IOPS** | 1,337 | 3,699 | 3,650 | **Ceph RBD** | **2.77x faster** |
| **Random Read IOPS** | 4,889 | 27,464 | 29,208 | **CephFS** | **5.98x faster** |
| **Sequential Write Latency** | 0.018 ms | 8.645 ms | 8.630 ms | **ZFS** | **480x lower** |
| **Sequential Read Latency** | 0.137 ms | 0.275 ms | 0.275 ms | **ZFS** | **2.0x lower** |
| **Random Write Latency** | 0.893 ms | 9.284 ms | 9.274 ms | **ZFS** | **10.4x lower** |
| **Random Read Latency** | 0.203 ms | 0.316 ms | 0.311 ms | **ZFS** | **1.56x lower** |

### kbench Key Findings

**ZFS Advantages:**

- **Ultra-low latency across all operations**: 10-480x lower latency than Ceph solutions
- **Best sequential write latency**: 0.018 ms (18 usec) - 480x lower than Ceph
- **Consistent low latency**: Best latency for all I/O patterns
- **Trade-off**: Lower throughput and IOPS compared to Ceph solutions

**Ceph RBD Advantages:**

- **Best sequential write throughput**: 168 MiB/s (1.95x faster than ZFS)
- **Good sequential read**: 650 MiB/s (16% faster than ZFS)
- **High random write IOPS**: 3,699 (2.77x higher than ZFS)
- **High random read IOPS**: 27,464 (5.6x higher than ZFS)
- **Trade-off**: Much higher latency (8-10x higher) than ZFS

**CephFS Advantages:**

- **Best sequential read throughput**: 820 MiB/s (1.46x faster than Ceph RBD, 1.46x faster than ZFS)
- **Best random read IOPS**: 29,208 (6x higher than ZFS, 6% higher than Ceph RBD)
- **ReadWriteMany support**: Can be mounted by multiple pods simultaneously
- **Trade-off**: Similar high latency to Ceph RBD (8-10x higher than ZFS)

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

## OpenEBS Mayastor (Single Replica)

**StorageClass**: `openebs-single-replica`
**Protocol**: NVMe over Fabrics (NVMe-oF/TCP)
**Test Node**: control-1 (TrueNAS ZVOL backend)

### Configuration: 2 CPU Cores (Default)

**CPU Configuration**: 2 cores (default Mayastor configuration)

#### Sequential Write (1MB blocks, 4 jobs)

- **Throughput**: 2,158 MiB/s (2,262 MB/s)
- **IOPS**: 2,157
- **Average Latency**: 1.84 ms
- **50th Percentile Latency**: 1.35 ms
- **99th Percentile Latency**: 11.1 ms

#### Sequential Read (1MB blocks, 4 jobs)

- **Throughput**: 3,573 MiB/s (3,747 MB/s)
- **IOPS**: 3,573
- **Average Latency**: 1.12 ms
- **50th Percentile Latency**: 1.07 ms
- **99th Percentile Latency**: 2.3 ms

#### Random Write (4KB blocks, 16 jobs)

- **Throughput**: 125 MiB/s (131 MB/s)
- **IOPS**: 32,000
- **Average Latency**: 0.50 ms
- **50th Percentile Latency**: 0.33 ms
- **99th Percentile Latency**: 3.2 ms

#### Random Read (4KB blocks, 16 jobs)

- **Throughput**: 397 MiB/s (416 MB/s)
- **IOPS**: 102,000
- **Average Latency**: 0.16 ms
- **50th Percentile Latency**: 0.17 ms
- **99th Percentile Latency**: 0.89 ms

### Configuration: 1 CPU Core (Optimized for Lower CPU Usage)

**CPU Configuration**: 1 core (`cpuCount: 1`, `coreList: [3]`)
**Optimization**: Reduced from 2 cores to 1 core to lower CPU usage

#### Sequential Write (1MB blocks, 4 jobs)

- **Throughput**: 133 MiB/s (140 MB/s)
- **IOPS**: 133
- **Average Latency**: 29.98 ms
- **50th Percentile Latency**: 16 ms
- **99th Percentile Latency**: 255 ms

#### Sequential Read (1MB blocks, 4 jobs)

- **Throughput**: 1,369 MiB/s (1,436 MB/s)
- **IOPS**: 1,369
- **Average Latency**: 2.92 ms
- **50th Percentile Latency**: 0.16 ms
- **99th Percentile Latency**: 129.5 ms

#### Random Write (4KB blocks, 16 jobs)

- **Throughput**: 58.7 MiB/s (61.5 MB/s)
- **IOPS**: 15,000
- **Average Latency**: 1.06 ms
- **50th Percentile Latency**: 0.44 ms
- **99th Percentile Latency**: 49.5 ms

#### Random Read (4KB blocks, 16 jobs)

- **Throughput**: 51.4 MiB/s (53.9 MB/s)
- **IOPS**: 13,100
- **Average Latency**: 1.22 ms
- **50th Percentile Latency**: 0.60 ms
- **99th Percentile Latency**: 50.1 ms

### CPU Optimization Comparison (1 Core vs 2 Cores)

| Metric | 2 Cores | 1 Core | Performance Impact | CPU Savings |
|--------|---------|--------|-------------------|-------------|
| **Sequential Write** | 2,158 MiB/s | 133 MiB/s | **16.2x slower** | **50% CPU reduction** |
| **Sequential Read** | 3,573 MiB/s | 1,369 MiB/s | **2.6x slower** | **50% CPU reduction** |
| **Random Write IOPS** | 32,000 | 15,000 | **2.1x slower** | **50% CPU reduction** |
| **Random Read IOPS** | 102,000 | 13,100 | **7.8x slower** | **50% CPU reduction** |
| **Sequential Write Latency (p50)** | 1.35 ms | 16 ms | **11.9x higher** | **50% CPU reduction** |
| **Sequential Read Latency (p50)** | 1.07 ms | 0.16 ms | **6.7x lower** | **50% CPU reduction** |
| **Random Write Latency (p50)** | 0.33 ms | 0.44 ms | **1.3x higher** | **50% CPU reduction** |
| **Random Read Latency (p50)** | 0.17 ms | 0.60 ms | **3.5x higher** | **50% CPU reduction** |

**Key Findings:**

- **CPU Usage**: Reduced by 50% (from 2 cores to 1 core)
- **Performance Impact**: Significant performance degradation across all metrics
  - Sequential writes are most affected (16.2x slower)
  - Random reads show the largest IOPS drop (7.8x slower)
  - Sequential reads are least affected (2.6x slower)
- **Use Case**: Suitable for workloads where CPU resources are constrained and maximum performance is not required
- **Recommendation**: Use 1-core configuration only when CPU usage is a primary concern and performance degradation is acceptable

### Mayastor (1 Core) vs ZFS Comparison

When Mayastor is limited to 1 CPU core, its performance characteristics change significantly compared to ZFS:

| Metric | Mayastor (1 Core) | ZFS | Winner | Difference |
|--------|------------------|-----|--------|------------|
| **Sequential Write** | 133 MiB/s | 132 MiB/s | **Tie** | 1.0x (essentially equal) |
| **Sequential Read** | 1,369 MiB/s | 4,711 MiB/s | **ZFS** | **3.4x faster** |
| **Random Write IOPS** | 15,000 | 9,237 | **Mayastor (1 Core)** | **1.6x faster** |
| **Random Read IOPS** | 13,100 | 59,300 | **ZFS** | **4.5x faster** |
| **Sequential Write Latency (p50)** | 16 ms | 9.9 ms | **ZFS** | 1.6x lower |
| **Sequential Read Latency (p50)** | 0.16 ms | 0.59 ms | **Mayastor (1 Core)** | **3.7x lower** |
| **Random Write Latency (p50)** | 0.44 ms | 0.052 ms | **ZFS** | **8.5x lower** |
| **Random Read Latency (p50)** | 0.60 ms | 0.21 ms | **ZFS** | **2.9x lower** |

**Key Findings:**

- **Sequential Writes**: Nearly identical performance (133 vs 132 MiB/s) - essentially a tie
- **Sequential Reads**: ZFS is 3.4x faster (4,711 vs 1,369 MiB/s), but Mayastor has 3.7x lower latency (0.16 vs 0.59 ms)
- **Random Writes**: Mayastor (1 core) achieves 1.6x higher IOPS (15,000 vs 9,237), but ZFS has 8.5x lower latency (0.052 vs 0.44 ms)
- **Random Reads**: ZFS significantly outperforms with 4.5x higher IOPS (59,300 vs 13,100) and 2.9x lower latency
- **CPU Usage**: Mayastor (1 core) uses 1 CPU core, while ZFS CPU usage is not explicitly limited but typically lower

**Recommendation for CPU-Constrained Environments:**

- **Choose Mayastor (1 core)** when:
  - Sequential write performance is critical and you need NVMe-oF protocol benefits
  - You need slightly better random write IOPS (1.6x advantage)
  - You can accept lower random read performance
  - You want consistent CPU usage (1 core dedicated)

- **Choose ZFS** when:
  - Sequential read performance is critical (3.4x faster)
  - Random read performance is important (4.5x higher IOPS)
  - Low latency for random I/O is required (especially random writes)
  - You want mature filesystem features (compression, snapshots, deduplication)
  - CPU usage can vary based on workload

---

**StorageClass**: `mayastor`
**Protocol**: NVMe over Fabrics (NVMe-oF/TCP)
**Replication**: 3 replicas across nodes
**Test Node**: control-1 (TrueNAS ZVOL backend)

### Sequential Write (1MB blocks, 4 jobs)

- **Throughput**: 129 MiB/s (135 MB/s)
- **IOPS**: 129
- **Average Latency**: 30.9 ms
- **50th Percentile Latency**: 31 ms
- **99th Percentile Latency**: 391 ms

### Sequential Read (1MB blocks, 4 jobs)

- **Throughput**: 2,784 MiB/s (2,919 MB/s)
- **IOPS**: 2,783
- **Average Latency**: 1.43 ms
- **50th Percentile Latency**: 0.1 ms
- **99th Percentile Latency**: 12.9 ms

### Random Write (4KB blocks, 16 jobs)

- **Throughput**: 19.6 MiB/s (20.6 MB/s)
- **IOPS**: 5,030
- **Average Latency**: 3.17 ms
- **50th Percentile Latency**: 0.96 ms
- **99th Percentile Latency**: 32.1 ms

### Random Read (4KB blocks, 16 jobs)

- **Throughput**: 1,035 MiB/s (1,085 MB/s)
- **IOPS**: 265,000
- **Average Latency**: 0.06 ms
- **50th Percentile Latency**: 0.0026 ms (2.6 usec)
- **99th Percentile Latency**: 0.85 ms

---

## OpenEBS ZFS LocalPV

**StorageClass**: `openebs-zfs`
**ZFS Pool**: `speed`
**ZFS Settings**:

- Record size: 128KB
- Compression: lz4
- Deduplication: off
- Thin provisioning: enabled

**Test Node**: control-1 (ZFS pool "speed")

### Sequential Write (1MB blocks, 4 jobs)

- **Throughput**: 132 MiB/s (138 MB/s)
- **IOPS**: 131
- **Average Latency**: 30.2 ms
- **50th Percentile Latency**: 9.9 ms
- **99th Percentile Latency**: 329 ms

### Sequential Read (1MB blocks, 4 jobs)

- **Throughput**: 4,711 MiB/s (4,940 MB/s)
- **IOPS**: 4,711
- **Average Latency**: 0.64 ms
- **50th Percentile Latency**: 0.59 ms
- **99th Percentile Latency**: 1.3 ms

### Random Write (4KB blocks, 16 jobs)

- **Throughput**: 36.1 MiB/s (37.8 MB/s)
- **IOPS**: 9,237
- **Average Latency**: 1.72 ms
- **50th Percentile Latency**: 0.052 ms (52 usec)
- **99th Percentile Latency**: 6.1 ms

### Random Read (4KB blocks, 16 jobs)

- **Throughput**: 232 MiB/s (243 MB/s)
- **IOPS**: 59,300
- **Average Latency**: 0.27 ms
- **50th Percentile Latency**: 0.21 ms
- **99th Percentile Latency**: 1.1 ms

---

## Rook Ceph (RBD - Block Storage)

**StorageClass**: `ceph-block`
**Protocol**: Ceph RBD (RADOS Block Device)
**Replication**: 3 replicas (Ceph default)
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
**Replication**: 3 replicas (Ceph default)
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

| Metric | Mayastor (Single) | Mayastor (3-Replica) | ZFS | Ceph RBD | CephFS | Winner | Difference |
|--------|------------------|---------------------|-----|----------|--------|--------|------------|
| **Sequential Write** | 2,158 MiB/s | 129 MiB/s | 132 MiB/s | 94.6 MiB/s | 127 MiB/s | **Mayastor (Single)** | **22.8x faster** |
| **Sequential Read** | 3,573 MiB/s | 2,784 MiB/s | 4,711 MiB/s | 6,906 MiB/s ⚠️ | 867 MiB/s | **Ceph RBD** ⚠️ | **1.5x faster** (cached) |
| **Random Write IOPS** | 32,000 | 5,030 | 9,237 | 862 | 953 | **Mayastor (Single)** | **37.1x faster** |
| **Random Read IOPS** | 102,000 | 265,000 | 59,300 | 1,674,000 ⚠️ | 22,200 | **Ceph RBD** ⚠️ | **75.5x faster** (cached) |
| **Random Write Latency (p50)** | 0.33 ms | 0.96 ms | 0.052 ms | 15 ms | 14 ms | **ZFS** | **269x lower** |
| **Random Read Latency (p50)** | 0.17 ms | 0.0026 ms | 0.21 ms | 0.0019 ms ⚠️ | 0.60 ms | **Ceph RBD** ⚠️ | **1.4x lower** (cached) |
| **Sequential Write Latency (p50)** | 1.35 ms | 31 ms | 9.9 ms | 31 ms | 30 ms | **Mayastor (Single)** | **22x lower** |
| **Sequential Read Latency (p50)** | 1.07 ms | 0.1 ms | 0.59 ms | 0.18 ms ⚠️ | 5 ms | **Mayastor (3-Replica)** | **50x lower** |

---

## Analysis and Recommendations

### Mayastor Advantages

**Single-Replica:**

- **Superior Sequential Write Performance**: 16.3x faster than ZFS (2,158 vs 132 MiB/s)
- **Much Higher Random Write IOPS**: 3.5x higher than ZFS (32,000 vs 9,237)
- **Higher Random Read IOPS**: 1.7x higher than ZFS (102,000 vs 59,300)
- **Lower Random Read Latency**: 1.2x lower than ZFS (0.17 vs 0.21 ms)
- **Lower Sequential Write Latency**: 7.3x lower than ZFS (1.35 vs 9.9 ms)

**3-Replica:**

- **Exceptional Random Read Performance**: 265,000 IOPS (4.5x higher than ZFS, 2.6x higher than single-replica)
- **Ultra-Low Random Read Latency**: 0.0026 ms (65x lower than ZFS, 65x lower than single-replica)
- **High Availability**: Data replicated across 3 nodes for fault tolerance
- **Excellent Sequential Read**: 2,784 MiB/s (comparable to single-replica, 21% slower than ZFS)

**Both:**

- **NVMe-oF Protocol**: Direct access to storage via NVMe over Fabrics provides low overhead
- **Replication Support**: Can be configured with multiple replicas for high availability

### ZFS Advantages

- **Superior Sequential Read Performance**: 1.7x faster than Mayastor single-replica (4,711 vs 3,573 MiB/s)
- **Lower Random Write Latency**: 18.5x lower than Mayastor 3-replica (0.052 vs 0.96 ms) - excellent for small random writes
- **Better Sequential Write than 3-Replica**: Comparable to Mayastor 3-replica (132 vs 129 MiB/s)
- **Advanced Features**: Built-in compression (lz4), snapshots, deduplication, and data integrity
- **Mature Technology**: Well-established filesystem with extensive tooling and documentation
- **Cost-Effective**: No additional hardware requirements beyond standard storage
- **No Replication Overhead**: Single-replica performance without the write penalty of multi-replica systems

### Use Case Recommendations

**Choose Mayastor Single-Replica when:**

- **Sequential write-heavy workloads** (databases, logging, analytics) - 16.3x faster sequential writes than ZFS
- **Random I/O intensive workloads** - 3.5x higher random write IOPS, 1.7x higher random read IOPS than ZFS
- **Large block I/O** - excels at sequential operations
- **Low latency requirements** - better random read latency than ZFS
- You have dedicated NVMe storage available
- **Data redundancy not critical** - single point of failure acceptable

**Choose Mayastor 3-Replica when:**

- **Ultra-high random read performance** - 265,000 IOPS (4.5x higher than ZFS, 2.6x higher than single-replica)
- **Ultra-low random read latency** - 0.0026 ms (65x lower than ZFS)
- **High availability required** - data replicated across 3 nodes
- **Read-heavy workloads with HA** - excellent sequential read (2,784 MiB/s) with redundancy
- **Acceptable write performance trade-off** - sequential writes much slower (129 vs 2,158 MiB/s) due to replication overhead

**Choose ZFS when:**

- **Read-heavy workloads** (media serving, backups, archives) - 1.7x faster sequential reads
- **Small random I/O** - better random write latency (3.5x lower) and random read IOPS (1.2x higher)
- You need advanced filesystem features (compression, snapshots, deduplication)
- Cost optimization is important
- You want mature, well-documented storage solution

### Ceph Advantages

**RBD (Block Storage):**

- **Exceptional Sequential Read Performance**: 6,906 MiB/s ⚠️ *Likely cached* (1.5x faster than ZFS, 1.9x faster than Mayastor single-replica)
- **Ultra-High Random Read IOPS**: 1,674,000 IOPS ⚠️ *Likely cached* (6.3x higher than Mayastor 3-replica, 28x higher than ZFS)
- **Ultra-Low Random Read Latency**: 0.0019 ms (1.4x lower than Mayastor 3-replica, 110x lower than ZFS)
- **⚠️ Cache Warning**: Read performance numbers are likely inflated due to Ceph's internal cache serving data after write tests
- **High Availability**: Built-in 3-replica replication with automatic failover
- **Enterprise-Grade**: Mature, production-tested distributed storage system
- **Scalability**: Can scale to thousands of nodes and petabytes of storage
- **Multiple Access Methods**: Supports both block (RBD) and filesystem (CephFS) interfaces

**Considerations:**

- **Sequential Write Performance**: Lower than Mayastor single-replica (94.6 vs 2,158 MiB/s) and ZFS (94.6 vs 132 MiB/s)
- **Random Write Performance**: Significantly lower IOPS (862 vs 32,000 for Mayastor single-replica) and higher latency (15 ms vs 0.33 ms)
- **Resource Usage**: Higher CPU and memory overhead compared to local storage solutions

**Choose Ceph RBD when:**

- **Ultra-high random read performance** - 1.67M IOPS ⚠️ *Likely cached* (6.3x higher than Mayastor 3-replica, 28x higher than ZFS)
- **Ultra-low random read latency** - 0.0019 ms (1.4x lower than Mayastor 3-replica)
- **Exceptional sequential read performance** - 6,906 MiB/s ⚠️ *Likely cached* (best among all tested solutions)
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
- **High Availability**: Built-in 3-replica replication with automatic failover
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
