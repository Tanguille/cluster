# VolSync Restore Guide

This guide explains how to properly restore data from VolSync backups.

## Understanding the Component

The `volsync` component creates:

- `ReplicationSource`: Creates backups from your PVC (hourly)
- `ReplicationDestination`: Used for restores
- `PVC`: Created from `ReplicationDestination`, which uses the `latestImage` snapshot

**Important**: When a PVC is created with `dataSourceRef` pointing to a `ReplicationDestination`, Kubernetes should use the ReplicationDestination's `latestImage` snapshot. However, the PVC spec is **immutable** after creation.

## Proper Restore Workflow

### Step 1: Suspend Kustomization AND HelmRelease

Prevent Flux from interfering during the restore. **Both** need to be suspended:

```bash
# Suspend the Kustomization (manages PVC and other resources)
flux suspend kustomization jellyfin -n media

# Suspend the HelmRelease (manages the Deployment)
flux suspend helmrelease jellyfin -n media
# OR
kubectl patch helmrelease jellyfin -n media --type merge -p '{"spec":{"suspend":true}}'
```

**Important**: The HelmRelease will keep scaling the deployment back up if not suspended!

### Step 2: Disable Auto-Scaling (if using KEDA/nfs-scaler)

If your app uses the `nfs-scaler` component, KEDA will auto-scale the deployment. Delete the ScaledObject:

```bash
kubectl delete scaledobject jellyfin -n media
```

### Step 3: Scale Down the Application

```bash
kubectl scale deployment jellyfin -n media --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/name=jellyfin -n media --timeout=60s
```

### Step 4: Delete the Existing PVC

```bash
kubectl delete pvc jellyfin -n media
kubectl wait --for=delete pvc/jellyfin -n media --timeout=60s
```

### Step 5: Configure Restore

Set the `restoreAsOf` timestamp to restore from a specific snapshot. Find available snapshots:

```bash
# List snapshots (use Kopia UI or check ReplicationSource status)
kubectl get replicationsource jellyfin -n media -o jsonpath='{.status.lastSyncTime}'
```

Configure the restore:

```bash
kubectl patch replicationdestination jellyfin-dst -n media --type merge -p '{
  "spec": {
    "kopia": {
      "restoreAsOf": "2025-12-14T20:14:36Z"
    },
    "trigger": {
      "manual": "restore-'$(date +%s)'"
    }
  }
}'
```

### Step 6: Wait for Restore to Complete

Monitor the restore progress:

```bash
# Watch for restore to complete
kubectl get replicationdestination jellyfin-dst -n media -o jsonpath='{.status.conditions[?(@.type=="Synchronizing")].status}'

# Wait until status is "False" and result is "Successful"
while true; do
  STATUS=$(kubectl get replicationdestination jellyfin-dst -n media -o jsonpath='{.status.conditions[?(@.type=="Synchronizing")].status}')
  RESULT=$(kubectl get replicationdestination jellyfin-dst -n media -o jsonpath='{.status.latestMoverStatus.result}')
  if [ "$STATUS" = "False" ] && [ "$RESULT" = "Successful" ]; then
    echo "Restore completed!"
    kubectl get replicationdestination jellyfin-dst -n media -o jsonpath='{.status.latestImage.name}'
    break
  fi
  echo "Waiting for restore... Status: $STATUS, Result: $RESULT"
  sleep 10
done
```

**Critical**: Note the `latestImage.name` - this is the snapshot that will be used.

### Step 7: Resume Kustomization and HelmRelease

This will recreate the PVC from the ReplicationDestination (which should use the `latestImage` snapshot) and resume the deployment:

```bash
# Resume the Kustomization (recreates PVC and ScaledObject)
flux resume kustomization jellyfin -n media

# Resume the HelmRelease (manages the Deployment)
flux resume helmrelease jellyfin -n media
```

### Step 8: Verify PVC Creation

Wait for the PVC to be created and bound:

```bash
kubectl wait --for=jsonpath='{.status.phase}'=Bound pvc/jellyfin -n media --timeout=120s
kubectl get pvc jellyfin -n media
```

**Verify**: The PVC should be created from the ReplicationDestination. Check that it's using the correct snapshot:

```bash
# The PVC's dataSourceRef should point to jellyfin-dst
kubectl get pvc jellyfin -n media -o jsonpath='{.spec.dataSourceRef.name}'
# Should output: jellyfin-dst

# The ReplicationDestination's latestImage should be the restored snapshot
kubectl get replicationdestination jellyfin-dst -n media -o jsonpath='{.status.latestImage.name}'
```

### Step 9: Scale Up the Application

```bash
kubectl scale deployment jellyfin -n media --replicas=1
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=jellyfin -n media --timeout=300s
```

### Step 10: Verify Data

```bash
kubectl exec -n media deployment/jellyfin -- du -sh /config
kubectl exec -n media deployment/jellyfin -- ls -la /config/data
```

## Troubleshooting

### Issue: PVC is created but empty

**Cause**: The PVC was created before the restore completed, or the ReplicationDestination's `latestImage` wasn't set yet.

**Solution**:

1. Ensure Step 5 completes fully before Step 6
2. Verify `latestImage.name` is set in ReplicationDestination status
3. If PVC is already created, you must delete it and let Flux recreate it

### Issue: Flux tries to change immutable PVC spec

**Cause**: The PVC was manually created from a snapshot, but the component template wants to create it from ReplicationDestination.

**Solution**:

1. Delete the PVC
2. Ensure restore is complete and `latestImage` is set
3. Resume kustomization to recreate PVC from ReplicationDestination

### Issue: Restore takes too long

Large restores can take 10-30 minutes. Be patient and monitor the ReplicationDestination status.

### Issue: Restore completes but `latestImage` is not set

**Symptoms**:

- `latestMoverStatus.result` is `Successful`
- `Synchronizing` condition is still `True`
- `latestImage` field is missing from ReplicationDestination status

**Cause**: The restore operation completed, but the snapshot creation step is stuck or taking a very long time. This can happen with large volumes (50Gi+) on Ceph storage.

**Solution**:

1. **Wait longer**: For large volumes, snapshot creation can take 30-60 minutes. Monitor the status:

   ```bash
   kubectl get replicationdestination nextcloud-dst -n default -o jsonpath='{.status.latestImage.name}'
   ```

2. **Check for VolumeSnapshots**: Verify if a snapshot is being created:

   ```bash
   kubectl get volumesnapshot -n default -l app.kubernetes.io/created-by=volsync | grep nextcloud-dst
   ```

3. **Re-trigger the restore**: If it's been stuck for over an hour, re-trigger with a new manual trigger:

   ```bash
   kubectl patch replicationdestination nextcloud-dst -n default --type merge -p '{
     "spec": {
       "trigger": {
         "manual": "restore-'$(date +%s)'"
       }
     }
   }'
   ```

4. **Check Ceph snapshot class**: Ensure the `volumeSnapshotClassName` is correct for Ceph:

   ```bash
   kubectl get volumesnapshotclass csi-ceph-blockpool
   ```

5. **Check for errors**: Look for any errors in the VolSync controller logs or VolumeSnapshot status:

   ```bash
   kubectl get volumesnapshot -n default -o yaml | grep -A 5 "error\|Error"
   ```

6. **Delete and recreate ReplicationDestination**: If the restore is completely stuck, you may need to delete the ReplicationDestination and let Flux recreate it. This will trigger a fresh restore:

   ```bash
   # First, ensure the kustomization is suspended
   flux suspend kustomization nextcloud -n default

   # Delete the ReplicationDestination
   kubectl delete replicationdestination nextcloud-dst -n default

   # Wait a moment, then resume the kustomization to recreate it
   flux resume kustomization nextcloud -n default

   # Then trigger a new restore
   kubectl patch replicationdestination nextcloud-dst -n default --type merge -p '{
     "spec": {
       "trigger": {
         "manual": "restore-'$(date +%s)'"
       }
     }
   }'
   ```

7. **Check if temporary PVC exists**: Verify that the temporary restore PVC exists and is bound:

   ```bash
   kubectl get pvc volsync-nextcloud-dst-dest -n default
   ```

   If the PVC exists but no snapshot was created, this indicates VolSync completed the restore but failed to create the snapshot. In this case, you may need to check Ceph CSI driver logs or VolSync controller logs for snapshot creation errors.

### Issue: Sync error on CephFS after restore completes

**Symptoms**:

- Restore completes successfully (data is restored)
- Error: `sync: error syncing '/restore/data': Invalid argument`
- `latestMoverStatus.result` is `Successful`
- `latestImage` is not updated (still points to old snapshot)

**Cause**: Known issue with CephFS and the `sync` command. The restore operation completes successfully, but the sync command fails, preventing snapshot creation.

**Solution**:

1. **Manual snapshot workaround**: Since the restore completes successfully, manually create a snapshot from the restore destination PVC:

   ```bash
   # Get the restore destination PVC name
   RESTORE_PVC=$(kubectl get pvc -n <namespace> | grep <app>-dst-dest | awk '{print $1}')

   # Create a manual snapshot
   cat <<EOF | kubectl apply -f -
   apiVersion: snapshot.storage.k8s.io/v1
   kind: VolumeSnapshot
   metadata:
     name: volsync-<app>-dst-dest-manual-$(date +%s | cut -c1-12)
     namespace: <namespace>
     labels:
       app.kubernetes.io/created-by: volsync
   spec:
     source:
       persistentVolumeClaimName: ${RESTORE_PVC}
     volumeSnapshotClassName: csi-ceph-filesystem
   EOF

   # Wait for snapshot to be ready
   kubectl wait --for=jsonpath='{.status.readyToUse}'=true volumesnapshot/<snapshot-name> -n <namespace> --timeout=120s
   ```

2. **Update ReplicationDestination status** (if needed): The manual snapshot can be used to create the PVC, but the ReplicationDestination's `latestImage` won't update automatically. The restore destination PVC contains the restored data and can be used directly.

3. **Prevention**: The CephFS storage class has been configured with mount options (`noatime`, `_netdev`) that may help reduce sync issues. These are applied to new PVCs.

## Key Points

1. **Always suspend the kustomization** before starting a restore
2. **Wait for restore to complete** before recreating the PVC
3. **The PVC must be created from ReplicationDestination**, not directly from snapshot
4. **The ReplicationDestination's `latestImage`** is what the PVC will use
5. **PVC spec is immutable** - if it's wrong, you must delete and recreate

## Future Improvements

Consider creating a script or Taskfile task to automate this workflow.
