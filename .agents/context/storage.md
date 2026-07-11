# Storage Context

**When to use:** Ceph, Rook, ceph-block, RWO, MON_DISK_LOW, monitor capacity, or ephemeral disk pressure.

- With `ceph-block` RWO storage, use Deployment strategy `Recreate`; RollingUpdate is unsupported.
- Ceph `mon_data_avail_warn` defaults to 30%. Low EPHEMERAL free percentage on a Talos monitor node can trigger `MON_DISK_LOW` despite substantial absolute free space.
- Fix node disk headroom first by pruning or expanding the VM disk or Talos layout. Lowering the threshold is a last resort.
