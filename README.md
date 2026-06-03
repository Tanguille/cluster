# Cluster

Welcome to my `fluxcd` kubernetes cluster running on `talos`. This is based on the [cluster-template](https://github.com/onedr0p/cluster-template) project where I want to express my gratitude to the community for all the amazing work they have done.

## Stats

<div align="center">

[![Talos](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Ftalos_version&style=for-the-badge&logo=talos&logoColor=white&color=blue&label=%20)](https://talos.dev)&nbsp;&nbsp;
[![Kubernetes](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Fkubernetes_version&style=for-the-badge&logo=kubernetes&logoColor=white&color=blue&label=%20)](https://kubernetes.io)&nbsp;&nbsp;
[![Flux](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Fflux_version&style=for-the-badge&logo=flux&logoColor=white&color=blue&label=%20)](https://fluxcd.io)&nbsp;&nbsp;

</div>

<div align="center">

[![Age-Days](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Fcluster_age_days&style=flat-square&label=Age)](https://kromgo.tanguille.site/)&nbsp;&nbsp;
[![Uptime-Days](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Fcluster_uptime_days&style=flat-square&label=Uptime)](https://kromgo.tanguille.site/)&nbsp;&nbsp;
[![Node-Count](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Fcluster_node_count&style=flat-square&label=Nodes)](https://kromgo.tanguille.site/)&nbsp;&nbsp;
[![Pod-Count](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Fcluster_pod_count&style=flat-square&label=Pods)](https://kromgo.tanguille.site/)&nbsp;&nbsp;
[![CPU-Usage](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Fcluster_cpu_usage&style=flat-square&label=CPU)](https://kromgo.tanguille.site/)&nbsp;&nbsp;
[![Memory-Usage](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Fcluster_memory_usage&style=flat-square&label=Memory)](https://kromgo.tanguille.site/)&nbsp;&nbsp;
[![Alerts](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.tanguille.site%2Fcluster_alert_count&style=flat-square&label=Alerts)](https://kromgo.tanguille.site/)

</div>

<div align="center">

[Live metrics gallery](https://kromgo.tanguille.site/) ·
[CPU graph](https://kromgo.tanguille.site/graphs/cluster_cpu_usage?last=24h) ·
[Memory graph](https://kromgo.tanguille.site/graphs/cluster_memory_usage?last=24h) ·
[Pods graph](https://kromgo.tanguille.site/graphs/cluster_pod_count?last=24h) ·
[Alerts graph](https://kromgo.tanguille.site/graphs/cluster_alert_count?last=24h)

</div>

## Architecture

This is a 3-node control plane Kubernetes cluster running on Talos Linux. All nodes serve as both control plane and worker nodes.

- **CNI:** Cilium
- **Storage:** Rook Ceph (block + filesystem) + OpenEBS Hostpath
- **Networking:** Cloudflare Tunnel, External DNS, Envoy Gateway, k8s-gateway

## Nodes

### Control Plane 1

TrueNAS VM:

- **CPU:** AMD Ryzen 5800X → 6 cores allocated to Talos
- **RAM:** 128GB DDR4 → 48GB allocated to Talos
- **GPU:** NVIDIA RTX 2070 → Full passthrough to Talos
- **Networking:** 10G NIC running at 2.5Gbps
- **Storage:** Samsung PM983 2TB nvme
  - Boot ZVOL: 500GB
  - Ceph ZVOL: 500GB

### Control Plane 2 & 3

Chuwi Ubox:

- **CPU:** AMD Ryzen 6600H (6 cores)
- **RAM:** 32GB DDR5
- **GPU:** AMD Radeon 660M (APU)
- **Networking:** 2x 2.5Gbps NICs (1 used)
- **Storage:**
  - Boot: Micron 7450 Pro (control-2: 500GB, control-3: 1TB) nvme
  - Ceph: (control-2: Samsung 980 Pro 1TB nvme, control-3: AirDisk 500GB SSD)
