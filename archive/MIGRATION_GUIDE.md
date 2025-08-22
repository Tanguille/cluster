# Radarr SQLite to PostgreSQL Migration Guide

This guide will help you migrate your Radarr instance from SQLite to PostgreSQL using CloudNativePG in your Kubernetes cluster.

## Prerequisites

- ✅ CloudNativePG PostgreSQL cluster running (you already have this)
- ✅ kubectl access to your cluster
- ✅ Backup of your current Radarr data

## Migration Overview

The migration process involves:

1. **Backup** - Create a backup of your current SQLite database
2. **Configuration Update** - Update Radarr to use PostgreSQL (init container handles DB creation)
3. **Data Migration** - Use pgloader to transfer data from SQLite to PostgreSQL
4. **Sequence Updates** - Fix PostgreSQL sequence values
5. **Verification** - Test that everything works correctly

## Step-by-Step Migration

### 1. Prepare Your Environment

First, ensure you have the necessary files in place:

```bash
# Check that all migration files are present
ls -la kubernetes/apps/media/radarr/
```

You should see:

- `secret.sops.yaml` - PostgreSQL credentials (encrypted)
- `helmrelease.yaml` - Updated Radarr configuration with init container
- `kustomization.yaml` - Updated to include the secret
- `migrate-to-postgres.sh` - Automated migration script

### 2. Update Your Secrets

**IMPORTANT**: Before proceeding, you need to encrypt your actual PostgreSQL credentials in the `secret.sops.yaml` file.

Replace the placeholder values with your actual encrypted credentials:

```yaml
POSTGRES_PASSWORD: ENC[AES256_GCM,data:YOUR_ACTUAL_ENCRYPTED_PASSWORD,iv:YOUR_IV,tag:YOUR_TAG,type:str]
INIT_POSTGRES_PASS: ENC[AES256_GCM,data:YOUR_ACTUAL_ENCRYPTED_PASSWORD,iv:YOUR_IV,tag:YOUR_TAG,type:str]
INIT_POSTGRES_SUPER_PASS: ENC[AES256_GCM,data:YOUR_SUPERUSER_PASSWORD,iv:YOUR_IV,tag:YOUR_TAG,type:str]
RADARR_API_KEY: ENC[AES256_GCM,data:YOUR_RADARR_API_KEY,iv:YOUR_IV,tag:YOUR_TAG,type:str]
TIMEZONE: ENC[AES256_GCM,data:YOUR_TIMEZONE,iv:YOUR_IV,tag:YOUR_TAG,type:str]
```

### 3. Apply Configuration Changes

Apply the updated configuration to your cluster:

```bash
# Apply the new secret and configuration
kubectl apply -k kubernetes/apps/media/radarr/app/

# Or use Flux if you're using GitOps
flux reconcile kustomization radarr --with-source
```

**Note**: The init container will automatically create the PostgreSQL database and user when Radarr starts for the first time.

### 4. Run the Migration

Use the automated migration script:

```bash
# Make the script executable
chmod +x kubernetes/apps/media/radarr/migrate-to-postgres.sh

# Run the migration
./kubernetes/apps/media/radarr/migrate-to-postgres.sh
```

**Or run the migration manually:**

```bash
# Step 1: Backup current data
kubectl scale deployment -n media radarr --replicas=0

# Wait for pod to terminate, then backup the SQLite database
# (The script handles this automatically)

# Step 2: Migrate data using pgloader
kubectl run radarr-migration --rm -i --tty --image dpage/pgloader --overrides='
{
  "spec": {
    "volumes": [
      {
        "name": "backup-data",
        "hostPath": {
          "path": "/tmp/radarr-migration",
          "type": "Directory"
        }
      }
    ],
    "containers": [
      {
        "name": "migration",
        "image": "dpage/pgloader",
        "command": ["/bin/bash"],
        "stdin": true,
        "tty": true,
        "volumeMounts": [
          {
            "name": "backup-data",
            "mountPath": "/backup"
          }
        ]
      }
    ]
  }
}' -- /bin/bash

# Once in the container:
cd /backup
POSTGRES_PASSWORD=$(kubectl get secret -n media radarr-secret -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)

pgloader --with "quote identifiers" \
         --with "data only" \
         --with "prefetch rows = 100" \
         --with "batch size = 1MB" \
         radarr.db.backup \
         postgresql://radarr:${POSTGRES_PASSWORD}@postgres16-rw.database.svc.cluster.local:5432/radarr
```

### 5. Update Sequence Values

After migration, update PostgreSQL sequence values:

```bash
# Connect to PostgreSQL and update sequences
PGPASSWORD=$(kubectl get secret -n media radarr-secret -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d) \
psql -h postgres16-rw.database.svc.cluster.local -U radarr -d radarr

# Run sequence updates (see the migration script for all sequences)
SELECT setval('public."MovieFiles_Id_seq"', COALESCE((SELECT MAX("Id")+1 FROM "MovieFiles"), 1));
SELECT setval('public."Movies_Id_seq"', COALESCE((SELECT MAX("Id")+1 FROM "Movies"), 1));
-- ... (run for all tables)
```

### 6. Restart Radarr

Scale Radarr back up with the new PostgreSQL configuration:

```bash
kubectl scale deployment -n media radarr --replicas=1

# Wait for pod to be ready
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=radarr -n media --timeout=300s
```

### 7. Verify Migration

Check that everything is working:

```bash
# Check Radarr logs
kubectl logs -n media -l app.kubernetes.io/name=radarr --tail=50

# Check database connection
kubectl exec -it -n media deployment/radarr -- env | grep RADARR__DB
```

## Troubleshooting

### Common Issues

1. **Database Connection Errors**
    - Verify PostgreSQL credentials in the secret
    - Check that the init container completed successfully
    - Ensure the PostgreSQL service is accessible from the media namespace

2. **Migration Failures**
    - Check pgloader logs for specific error messages
    - Verify the SQLite database is accessible
    - Ensure sufficient disk space for the migration

3. **Sequence Errors**
    - Run the sequence update commands manually
    - Check that all tables were migrated correctly

### Rollback Plan

If you need to rollback:

```bash
# Scale down Radarr
kubectl scale deployment -n media radarr --replicas=0

# Restore the SQLite database from backup
# (Copy the backup back to the config volume)

# Scale Radarr back up
kubectl scale deployment -n media radarr --replicas=1
```

## Benefits of PostgreSQL

After migration, you'll have:

- ✅ **Better Performance** - Improved query performance for large libraries
- ✅ **Scalability** - Better handling of concurrent operations
- ✅ **Reliability** - ACID compliance and better data integrity
- ✅ **Backup Integration** - Leverage your existing CloudNativePG backup strategy
- ✅ **Monitoring** - Better integration with your existing PostgreSQL monitoring
- ✅ **Automatic Setup** - Init container handles database creation automatically

## Post-Migration Tasks

1. **Monitor Performance** - Watch for any performance improvements or issues
2. **Update Backups** - Ensure your backup strategy includes the new PostgreSQL data
3. **Clean Up** - Remove old SQLite backups once you're confident in the migration
4. **Documentation** - Update any documentation that referenced SQLite

## Support

If you encounter issues:

1. Check the Radarr logs: `kubectl logs -n media -l app.kubernetes.io/name=radarr`
2. Verify PostgreSQL connectivity: `kubectl exec -it deployment/radarr -- nc -zv postgres16-rw.database.svc.cluster.local 5432`
3. Check database permissions: Connect to PostgreSQL and verify user permissions
4. Review the migration script output for any error messages

Remember: **Always test the migration process in a non-production environment first!**
