# kube-prometheus-stack

## Truenas Deployments

### node-exporter

```yaml
services:
    node-exporter:
        command:
            - "--path.rootfs=/host"
        container_name: node_exporter
        image: quay.io/prometheus/node-exporter:latest
        network_mode: host
        pid: host
        restart: always
        volumes:
            - /:/host:ro,rslave
```

### smartctl-exporter

```yaml
services:
    smartctl-exporter:
        image: quay.io/prometheuscommunity/smartctl-exporter:latest
        ports:
            - "9633:9633"
        privileged: True
        restart: always
        user: root
```
