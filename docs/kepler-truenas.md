# Kepler on TrueNAS (control-1 VM host)

control-1 runs as a VM on TrueNAS and has no RAPL, so the in-cluster Kepler DaemonSet cannot report CPU power there. Run Kepler in a container **on the TrueNAS host** with the fake CPU meter so the dashboard gets control-1’s (estimated) CPU power. Prometheus scrapes that exporter and we relabel it as `job=power-monitor`, `instance=control-1`.

**GPU:** The GPU is passed through to Talos (control-1), not the TrueNAS host, so the TrueNAS Kepler container will never see it (`NVML init failed` / `no GPUs discovered` is expected). GPU power is collected by the **in-cluster** Kepler DaemonSet on control-1 (GPU is enabled in the PowerMonitor via `kepler-gpu-config`). Leave GPU disabled in the TrueNAS compose to avoid log noise.

## 1. Config for Kepler (fake CPU meter + optional GPU)

On TrueNAS (or your workstation), create a config file. At minimum enable the fake CPU meter; if the host has an NVIDIA GPU you want in power metrics, enable the experimental GPU feature:

```yaml
# config.yaml (e.g. in ./default/kepler/etc/kepler/config.yaml for compose)
log:
  level: info
exporter:
  prometheus:
    enabled: true
dev:
  fake-cpu-meter:
    enabled: true
# Optional: include GPU power (NVIDIA). Requires container GPU access (see below).
experimental:
  gpu:
    enabled: true   # set to true to report GPU power
    idlePower: 0    # Watts when idle (0 = auto-detect)
```

## 2. Single-file Compose (config inside the compose)

One compose file, no separate config file. The `command` writes the config from a heredoc to `/tmp/config.yaml` then runs Kepler. Works on TrueNAS without `configs` support.

```yaml
# compose.yaml — only file you need
services:
  kepler:
    image: quay.io/sustainable_computing_io/kepler:latest
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        cat > /tmp/config.yaml << 'KEPLEREOF'
        log:
          level: info
        host:
          sysfs: /sys
          procfs: /proc
        exporter:
          prometheus:
            enabled: true
            metricsLevel:
              - node
              - process
              - container
              - vm
              - pod
        dev:
          fake-cpu-meter:
            enabled: true
        experimental:
          gpu:
            enabled: false
        KEPLEREOF
        exec kepler --config.file=/tmp/config.yaml
    ports:
      - "28284:28282"
    privileged: true
    volumes:
      - type: bind
        source: /proc
        target: /host/proc
        read_only: true
      - type: bind
        source: /sys
        target: /host/sys
        read_only: true
```

- To change the config, edit the YAML between `KEPLEREOF` and `KEPLEREOF` in the compose file.
- Do **not** add a GPU `deploy` block unless the host has the NVIDIA Container Toolkit, or the container may fail to start.
- If the image has no `/bin/sh` (distroless), this will fail; then use the two-file setup (compose + bind-mounted `config.yaml`) from the “Run Kepler” section below.

## 3. Run Kepler in a container on TrueNAS (other options)

**Option A – TrueNAS SCALE “Launch Docker Image”**

1. Apps → Launch Docker Image.
2. Image: `quay.io/sustainable_computing_io/kepler:latest` (or a fixed tag, e.g. `v1.0.0`).
3. Port: container port `28282` → host port `28282` (so the cluster can scrape it).
4. Add a volume: mount the path where you saved `kepler-truenas-config.yaml` (e.g. `/path/on/truenas/config.yaml`) into the container at `/etc/kepler/config.yaml`.
5. Command/args (if the image supports it): `--config.file=/etc/kepler/config.yaml`. If the image uses a different path, adjust. Some images read a config from an env var; check the image docs.
6. Ensure the TrueNAS host firewall allows inbound TCP 28282 from the Kubernetes cluster (e.g. from the Prometheus nodes or your cluster CIDR).

**Option B – Docker / Podman on TrueNAS (CLI)**

If you have Docker or Podman on the TrueNAS host:

```bash
# Create config (adjust path as needed); add experimental.gpu.enabled: true if you have an NVIDIA GPU
mkdir -p /path/on/truenas/kepler
# ... create config.yaml with fake-cpu-meter and optional experimental.gpu (see above) ...

docker run -d --restart=unless-stopped \
  --name kepler-truenas \
  -p 28282:28282 \
  -v /path/on/truenas/kepler/config.yaml:/etc/kepler/config.yaml:ro \
  -v /proc:/host/proc:ro \
  -v /sys:/host/sys:ro \
  --privileged \
  quay.io/sustainable_computing_io/kepler:latest \
  --config.file=/etc/kepler/config.yaml
```

For **GPU power** (NVIDIA): ensure the container can see the GPU. With the NVIDIA Container Toolkit on the host, add `--gpus all` to the `docker run` (or in compose use `deploy.resources.reservations.devices: - driver: nvidia; count: all; capabilities: [gpu]`). Kepler’s GPU support is experimental and uses the host’s NVIDIA driver/DCGM; the container must have GPU access.

(With Podman, replace `docker` with `podman` and add `--network host` if you prefer to bind 28282 on the host.)

## 4. Cluster-side scrape (already configured)

The repo already defines a Prometheus `ScrapeConfig` that:

- Scrapes `TRUENAS_IP:28282` (see `kubernetes/components/common/cluster-settings.yaml` for `TRUENAS_IP`).
- Sets `job=power-monitor` and `instance=control-1`, and overwrites `node_name` to `control-1`.

So once Kepler is running on TrueNAS and reachable on port 28282 from the cluster, the Kepler “Power Monitor” dashboard will show **control-1** with (estimated) power from the fake CPU meter.

## 5. Checks

- From a pod in the cluster: `curl http://<TRUENAS_IP>:28282/metrics` and look for `kepler_` metrics.
- In Prometheus → Status → Targets, the `kepler-truenas` target should be UP.
- In Grafana, open the Kepler dashboard, choose **Node: control-1** and **Job: power-monitor**; you should see time series for control-1.

## Note

The fake CPU meter is for environments without hardware power sensors (e.g. VMs). Values are estimated, not measured. Only this single exporter on TrueNAS uses it; the rest of the cluster keeps using RAPL where available.
