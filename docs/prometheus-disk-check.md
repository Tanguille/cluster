# Prometheus "no space left on device" – checks and recovery

## Quick checks (observability namespace)

```bash
# 1. Actual PVC size and usage
kubectl get pvc -n observability -l app.kubernetes.io/name=prometheus
kubectl exec -n observability prometheus-kube-prometheus-stack-0 -c prometheus -- df -h /prometheus

# 2. Ceph pool capacity (if you use Rook/Ceph)
kubectl exec -n rook-ceph deploy/rook-ceph-operator -- ceph df

# 3. Prometheus data dir size inside the pod
kubectl exec -n observability prometheus-kube-prometheus-stack-0 -c prometheus -- du -sh /prometheus/*
```

## Recovery when Prometheus can't start (no space left)

Prometheus fails **before** it can run retention, so we must free space from outside.

### Option A: Resize the PVC (ceph-block supports expansion)

If the PVC is 100Gi and full, resize it to 110Gi so there is headroom for compaction/WAL:

```bash
# Resize the Prometheus PVC
kubectl patch pvc prometheus-kube-prometheus-stack-db-prometheus-kube-prometheus-stack-0 -n observability -p '{"spec":{"resources":{"requests":{"storage":"110Gi"}}}}'

# Restart Prometheus so it remounts and sees the new size (Ceph expands online)
kubectl delete pod -n observability prometheus-kube-prometheus-stack-0
```

Then confirm the volume size and that the pod is Running:

```bash
kubectl get pvc -n observability prometheus-kube-prometheus-stack-db-prometheus-kube-prometheus-stack-0
kubectl get pod -n observability -l app.kubernetes.io/name=prometheus -w
```

### Option B: Free space by deleting old blocks (if resize isn't possible or Ceph pool is full)

1. Scale down Prometheus so the PVC is detached:

   ```bash
   kubectl scale statefulset prometheus-kube-prometheus-stack -n observability --replicas=0
   ```

2. Run a one-off pod that mounts the same PVC and deletes the oldest block dirs to free space:

   ```bash
   kubectl run -n observability prometheus-disk-free --rm -it --restart=Never \
     --image=alpine:3.19 \
     --overrides='{"spec":{"containers":[{"name":"free","image":"alpine:3.19","command":["/bin/sh","-c","apk add --no-cache findutil coreutils && ls -la /data && du -sh /data/* | sort -h && echo \"Deleting oldest blocks...\" && cd /data && ls -d 01* 2>/dev/null | sort | head -20 | xargs -r rm -rf && du -sh /data && echo Done"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"prometheus-kube-prometheus-stack-db-prometheus-kube-prometheus-stack-0"}}]}}'
   ```

   (Adjust the `ls -d 01*` / `head -20` if your blocks use a different naming; Prometheus block dirs are typically `01H*`-style. The goal is to remove the oldest blocks only.)

3. Scale Prometheus back up:

   ```bash
   kubectl scale statefulset prometheus-kube-prometheus-stack -n observability --replicas=1
   ```

### Option C: Nuclear – new empty volume (full data loss)

Delete the PVC and the Prometheus pod; the StatefulSet will recreate a new PVC (110Gi from the Helm template) and Prometheus will start empty:

```bash
kubectl delete pod -n observability prometheus-kube-prometheus-stack-0
kubectl delete pvc -n observability prometheus-kube-prometheus-stack-db-prometheus-kube-prometheus-stack-0
# StatefulSet will recreate the pod and a new PVC
```
