#!/bin/bash
set -euo pipefail

# pv-migrate migration script for ZFS to Ceph migrations
# This script migrates PVCs from openebs-zfs to ceph-block storage class
# Volsync-backed PVCs are automatically skipped as they can be restored easily
#
# Usage: ./migrate-pvs.sh [--dry-run] [--yes] [--swap]
#   --dry-run  Show what would be done without making changes
#   --yes      Skip confirmation prompts
#   --swap     Automatically swap PVCs to keep original name (requires app scale-down)

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly NC='\033[0m' # No Color

# Storage classes
readonly SOURCE_SC="openebs-zfs"
readonly DEST_SC="ceph-block"

# ZFS node - required for ZFS LocalPV access
readonly ZFS_NODE="control-1"

# Parse arguments
DRY_RUN=false
AUTO_YES=false
AUTO_SWAP=false
for arg in "$@"; do
    case $arg in
        --dry-run) DRY_RUN=true ;;
        --yes|-y) AUTO_YES=true ;;
        --swap) AUTO_SWAP=true ;;
        --help|-h)
            echo "Usage: $0 [--dry-run] [--yes] [--swap]"
            echo "  --dry-run  Show what would be done without making changes"
            echo "  --yes      Skip confirmation prompts"
            echo "  --swap     Automatically swap PVCs to keep original name"
            echo "             (This will scale down the app, swap PVCs, and scale back up)"
            exit 0
            ;;
    esac
done

# Logging functions
log_info() { echo -e "${BLUE}ℹ $*${NC}"; }
log_success() { echo -e "${GREEN}✓ $*${NC}"; }
log_warning() { echo -e "${YELLOW}⚠ $*${NC}"; }
log_error() { echo -e "${RED}✗ $*${NC}"; }
log_step() { echo -e "${BLUE}$*${NC}"; }

# Check dependencies
check_dependencies() {
    local missing=()

    if ! command -v kubectl &>/dev/null; then
        missing+=("kubectl")
    elif ! kubectl pv-migrate --help &>/dev/null; then
        missing+=("kubectl pv-migrate plugin (install: kubectl krew install pv-migrate)")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing dependencies:"
        for dep in "${missing[@]}"; do
            echo "  - $dep"
        done
        exit 1
    fi
}

# Function to check if PVC uses volsync
check_volsync() {
    local namespace=$1
    local pvc_name=$2

    local datasource_kind
    datasource_kind=$(kubectl get pvc -n "$namespace" "$pvc_name" \
        -o jsonpath='{.spec.dataSourceRef.kind}' 2>/dev/null || true)

    [[ "$datasource_kind" == "ReplicationDestination" ]] && return 0

    datasource_kind=$(kubectl get pvc -n "$namespace" "$pvc_name" \
        -o jsonpath='{.spec.dataSource.kind}' 2>/dev/null || true)

    [[ "$datasource_kind" == "ReplicationDestination" ]] && return 0

    return 1
}

# Function to get PVC storage class
get_pvc_storage_class() {
    local namespace=$1
    local pvc_name=$2
    kubectl get pvc -n "$namespace" "$pvc_name" \
        -o jsonpath='{.spec.storageClassName}' 2>/dev/null || true
}

# Function to cleanup stuck pv-migrate resources
cleanup_stuck_migrations() {
    log_info "Checking for stuck pv-migrate resources..."

    local stuck_helm_releases
    stuck_helm_releases=$(helm list -A -q | grep "^pv-migrate-" 2>/dev/null || true)

    if [[ -n "$stuck_helm_releases" ]]; then
        log_warning "Found stuck pv-migrate Helm releases:"
        echo "$stuck_helm_releases"
        echo ""

        if confirm "Clean up stuck pv-migrate resources?"; then
            for release in $stuck_helm_releases; do
                local namespace
                namespace=$(helm list -A | grep "$release" | awk '{print $2}' || echo "default")
                log_info "Uninstalling $release from $namespace..."
                if $DRY_RUN; then
                    log_info "[DRY-RUN] Would run: helm uninstall $release -n $namespace"
                else
                    helm uninstall "$release" -n "$namespace" 2>/dev/null || true
                fi
            done
            log_success "Cleanup complete"
        fi
    else
        log_success "No stuck pv-migrate resources found"
    fi
    echo ""
}

# Function to ensure ZFS CSI driver is available
ensure_zfs_driver() {
    log_info "Ensuring ZFS CSI driver is available..."

    # Check if ZFS driver pods are running
    local zfs_pods
    zfs_pods=$(kubectl get pods -n openebs-system -l app=zfs-localpv --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d '[:space:]')
    zfs_pods=${zfs_pods:-0}   # Default to 0 if empty
    # Force to integer
    zfs_pods=$((10#$zfs_pods))

    if [[ $zfs_pods -gt 0 ]]; then
        log_success "ZFS CSI driver pods are running ($zfs_pods pod(s))"
        return 0
    fi

    log_warning "ZFS CSI driver pods are not running"

    # Check if node exists
    if ! kubectl get node "$ZFS_NODE" &>/dev/null; then
        log_error "Node $ZFS_NODE not found!"
        return 1
    fi

    # Check if node has the required label
    local node_label
    node_label=$(kubectl get node "$ZFS_NODE" -o jsonpath='{.metadata.labels.storage\.zfs/available}' 2>/dev/null || echo "")

    if [[ "$node_label" != "true" ]]; then
        log_warning "Node $ZFS_NODE is not labeled with storage.zfs/available=true"
        log_info "Labeling node to enable ZFS driver pods..."

        if $DRY_RUN; then
            log_info "[DRY-RUN] Would label: kubectl label node $ZFS_NODE storage.zfs/available=true --overwrite"
        else
            if kubectl label node "$ZFS_NODE" storage.zfs/available=true --overwrite 2>/dev/null; then
                log_success "Node labeled"

                # Wait for ZFS driver pods to start
                log_info "Waiting for ZFS CSI driver pods to start..."
                local wait_count=0
                while [[ $wait_count -lt 60 ]]; do
                    zfs_pods=$(kubectl get pods -n openebs-system -l app=zfs-localpv --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d '[:space:]')
                    zfs_pods=${zfs_pods:-0}   # Default to 0 if empty
                    # Force to integer
                    zfs_pods=$((10#$zfs_pods))
                    if [[ $zfs_pods -gt 0 ]]; then
                        log_success "ZFS CSI driver pods are running ($zfs_pods pod(s))"
                        return 0
                    fi
                    sleep 2
                    ((wait_count++))
                    if [[ $((wait_count % 10)) -eq 0 ]]; then
                        log_info "  Still waiting... (${wait_count}s)"
                    fi
                done

                log_warning "ZFS driver pods did not start within 120s"
                log_warning "Migration may fail if driver is not available"
                log_info "Check: kubectl get pods -n openebs-system -l app=zfs-localpv"
            else
                log_error "Failed to label node"
                return 1
            fi
        fi
    else
        log_info "Node is labeled, but ZFS pods are not running"
        log_warning "ZFS may be disabled or pods failed to start"
        log_warning "Check: kubectl get pods -n openebs-system -l app=zfs-localpv"
        log_warning "Check: kubectl get helmrelease -n openebs-system openebs"
    fi

    echo ""
}

# Function to get PVC size
get_pvc_size() {
    local namespace=$1
    local pvc_name=$2
    kubectl get pvc -n "$namespace" "$pvc_name" \
        -o jsonpath='{.spec.resources.requests.storage}' 2>/dev/null || true
}

# Function to get PVC access modes
get_pvc_access_modes() {
    local namespace=$1
    local pvc_name=$2
    kubectl get pvc -n "$namespace" "$pvc_name" \
        -o jsonpath='{.spec.accessModes[0]}' 2>/dev/null || true
}

# Confirm action (respects AUTO_YES and DRY_RUN)
confirm() {
    local prompt=$1
    if $DRY_RUN; then
        log_info "[DRY-RUN] Would prompt: $prompt"
        return 0
    fi
    if $AUTO_YES; then
        return 0
    fi
    read -p "$prompt (y/N) " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]]
}

# Function to swap PVCs (delete old, rebind new with original name)
swap_pvcs() {
    local namespace=$1
    local original_name=$2
    local temp_name=$3
    local pvc_size=$4
    local pvc_access_mode=$5
    local dest_storage_class=${6:-"$DEST_SC"}

    log_step "Starting PVC swap to keep original name '${original_name}'..."

    if $DRY_RUN; then
        log_info "[DRY-RUN] Would swap PVCs:"
        echo "  1. Get PV name from ${temp_name}"
        echo "  2. Set PV reclaim policy to Retain"
        echo "  3. Delete old PVC ${original_name}"
        echo "  4. Delete temp PVC ${temp_name}"
        echo "  5. Remove claimRef from PV"
        echo "  6. Create new PVC ${original_name} bound to the PV"
        return 0
    fi

    # Step 1: Get the PV name from the temp PVC
    local pv_name
    pv_name=$(kubectl get pvc -n "$namespace" "$temp_name" -o jsonpath='{.spec.volumeName}')
    if [[ -z "$pv_name" ]]; then
        log_error "Could not get PV name from PVC ${temp_name}"
        return 1
    fi
    log_info "PV name: ${pv_name}"

    # Step 2: Set PV reclaim policy to Retain
    log_step "  Setting PV reclaim policy to Retain..."
    kubectl patch pv "$pv_name" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
    log_success "PV reclaim policy set to Retain"

    # Step 3: Delete the old source PVC
    log_step "  Deleting old source PVC '${original_name}'..."
    if ! kubectl delete pvc -n "$namespace" "$original_name" --wait=true --timeout=60s 2>/dev/null; then
        log_warning "PVC deletion timed out or failed, checking finalizers..."
        local finalizers
        finalizers=$(kubectl get pvc -n "$namespace" "$original_name" -o jsonpath='{.metadata.finalizers[*]}' 2>/dev/null || echo "")
        if [[ -n "$finalizers" ]]; then
            log_warning "PVC has finalizers: ${finalizers}"
            log_info "Attempting to remove finalizers..."
            kubectl patch pvc -n "$namespace" "$original_name" --type json -p '[{"op": "remove", "path": "/metadata/finalizers"}]' 2>/dev/null || true
            sleep 2
        fi
        # Try again without wait
        kubectl delete pvc -n "$namespace" "$original_name" --wait=false 2>/dev/null || true
    fi
    log_success "Old PVC deleted"

    # Step 4: Delete the temp PVC (PV will remain due to Retain policy)
    log_step "  Deleting temp PVC '${temp_name}'..."
    if ! kubectl delete pvc -n "$namespace" "$temp_name" --wait=true --timeout=60s 2>/dev/null; then
        log_warning "Temp PVC deletion timed out, checking finalizers..."
        local finalizers
        finalizers=$(kubectl get pvc -n "$namespace" "$temp_name" -o jsonpath='{.metadata.finalizers[*]}' 2>/dev/null || echo "")
        if [[ -n "$finalizers" ]]; then
            log_warning "Temp PVC has finalizers: ${finalizers}"
            log_info "Attempting to remove finalizers..."
            kubectl patch pvc -n "$namespace" "$temp_name" --type json -p '[{"op": "remove", "path": "/metadata/finalizers"}]' 2>/dev/null || true
            sleep 2
        fi
        # Try again without wait
        kubectl delete pvc -n "$namespace" "$temp_name" --wait=false 2>/dev/null || true
    fi
    log_success "Temp PVC deleted"

    # Wait for PV to be Released
    log_step "  Waiting for PV to be Released..."
    sleep 2

    # Step 5: Remove claimRef from PV to make it Available
    log_step "  Removing claimRef from PV..."
    kubectl patch pv "$pv_name" --type json -p '[{"op": "remove", "path": "/spec/claimRef"}]'
    log_success "claimRef removed"

    # Wait for PV to be Available
    log_step "  Waiting for PV to be Available..."
    local attempts=0
    local max_attempts=60
    while [[ $(kubectl get pv "$pv_name" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown") != "Available" ]]; do
        local current_phase
        current_phase=$(kubectl get pv "$pv_name" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
        if [[ $((attempts % 5)) -eq 0 ]]; then
            log_info "  PV phase: ${current_phase} (attempt ${attempts}/${max_attempts})"
        fi
        sleep 1
        ((attempts++))
        if [[ $attempts -gt $max_attempts ]]; then
            log_error "PV did not become Available in time (current phase: ${current_phase})"
            log_info "PV details:"
            kubectl get pv "$pv_name" -o yaml | grep -A 5 "spec:" || true
            return 1
        fi
    done
    log_success "PV is Available"

    # Step 6: Create new PVC with original name bound to the PV
    log_step "  Creating new PVC '${original_name}' bound to PV..."
    cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${original_name}
  namespace: ${namespace}
spec:
  accessModes:
    - ${pvc_access_mode}
  storageClassName: ${dest_storage_class}
  resources:
    requests:
      storage: ${pvc_size}
  volumeName: ${pv_name}
EOF

    # Wait for new PVC to be bound
    log_step "  Waiting for new PVC to be Bound..."
    local attempts=0
    local max_attempts=60
    while [[ $(kubectl get pvc -n "$namespace" "$original_name" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown") != "Bound" ]]; do
        local current_phase
        current_phase=$(kubectl get pvc -n "$namespace" "$original_name" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
        if [[ $((attempts % 5)) -eq 0 ]]; then
            log_info "  PVC phase: ${current_phase} (attempt ${attempts}/${max_attempts})"
            if [[ "$current_phase" == "Pending" ]]; then
                log_info "  Checking PVC events..."
                kubectl describe pvc -n "$namespace" "$original_name" | tail -5 || true
            fi
        fi
        sleep 1
        ((attempts++))
        if [[ $attempts -gt $max_attempts ]]; then
            log_error "New PVC did not become Bound in time (current phase: ${current_phase})"
            log_info "PVC details:"
            kubectl get pvc -n "$namespace" "$original_name" -o yaml | grep -A 10 "status:" || true
            return 1
        fi
    done

    log_success "PVC swap completed! '${original_name}' now uses ${dest_storage_class}"
}

# Function to migrate a specific PVC
migrate_pvc() {
    local namespace=$1
    local pvc_name=$2
    local description=${3:-"$pvc_name ($namespace)"}
    local dest_storage_class=${4:-"$DEST_SC"}  # Allow per-PVC destination storage class

    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Migration: ${description}${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    # Check if source PVC exists first
    if ! kubectl get pvc -n "$namespace" "$pvc_name" &>/dev/null; then
        log_warning "PVC '${pvc_name}' not found in '${namespace}' namespace, skipping"
        echo ""
        return 0
    fi

    # Get current storage class for display and validation
    local current_sc
    current_sc=$(get_pvc_storage_class "$namespace" "$pvc_name")

    if [[ -z "$current_sc" ]]; then
        log_warning "Could not determine storage class for PVC '${pvc_name}', skipping"
        echo ""
        return 0
    fi

    echo "  Namespace:   ${namespace}"
    echo "  PVC:         ${pvc_name}"
    echo "  Source SC:   ${current_sc}"
    echo "  Dest SC:     ${dest_storage_class}"
    if [[ "$current_sc" == "$SOURCE_SC" ]]; then
        echo "  ZFS Node:    ${ZFS_NODE}"
    fi
    $DRY_RUN && echo -e "  ${YELLOW}[DRY-RUN MODE]${NC}"
    $AUTO_SWAP && echo -e "  ${CYAN}[AUTO-SWAP: Will keep original PVC name]${NC}"
    echo ""

    # Check if already on destination storage class
    if [[ "$current_sc" == "$dest_storage_class" ]]; then
        log_success "PVC '${pvc_name}' already uses ${dest_storage_class}, skipping"
        echo ""
        return 0
    fi

    # Source storage class doesn't matter - we can migrate from any to any
    # Just log what we're doing
    if [[ "$current_sc" == "$dest_storage_class" ]]; then
        # This case is already handled above, but just in case
        log_success "PVC '${pvc_name}' already uses ${dest_storage_class}, skipping"
        echo ""
        return 0
    fi

    log_info "Migrating from ${current_sc} to ${dest_storage_class}"

    # Check if PVC uses volsync
    if check_volsync "$namespace" "$pvc_name"; then
        log_warning "PVC '${pvc_name}' uses volsync (ReplicationDestination), skipping"
        log_info "Volsync PVCs can be restored from backups"
        echo ""
        return 0
    fi

    # Check if ZFS CSI driver is available (if using ZFS storage)
    if [[ "$current_sc" == "$SOURCE_SC" ]]; then
        local zfs_driver_available
        zfs_driver_available=$(kubectl get csidriver zfs.csi.openebs.io 2>/dev/null && echo "yes" || echo "no")
        if [[ "$zfs_driver_available" == "no" ]]; then
            log_warning "ZFS CSI driver (zfs.csi.openebs.io) is not available!"
            log_info "This will cause mount failures for pods using ZFS PVCs."
            echo ""
            log_info "Checking for pods using this PVC..."
            local pods_using_pvc
            pods_using_pvc=$(kubectl get pods -n "$namespace" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.volumes[*].persistentVolumeClaim.claimName}{"\n"}{end}' 2>/dev/null | grep "\b${pvc_name}\b" | awk '{print $1}' || true)
            if [[ -n "$pods_using_pvc" ]]; then
                log_error "Found pods using this PVC: $pods_using_pvc"
                log_warning "These pods will fail to mount without the ZFS driver!"
                echo ""
                log_info "You must either:"
                echo "  1. Scale down pods using this PVC before migration, OR"
                echo "  2. Ensure ZFS CSI driver is installed/running"
                echo ""
                if ! confirm "Continue anyway? (pods will likely fail)"; then
                    log_warning "Skipped ${pvc_name} migration"
                    echo ""
                    return 0
                fi
            fi
        fi
    fi

    # Get PVC details
    local pvc_size pvc_access_mode
    pvc_size=$(get_pvc_size "$namespace" "$pvc_name")
    pvc_access_mode=$(get_pvc_access_modes "$namespace" "$pvc_name")

    # Temporary destination PVC name (include storage class hint if not default)
    local storage_class_suffix
    if [[ "$dest_storage_class" != "$DEST_SC" ]]; then
        storage_class_suffix=$(echo "$dest_storage_class" | sed 's/ceph-//' | sed 's/-//g')
        local temp_pvc_name="${pvc_name}-${storage_class_suffix}-temp"
    else
        local temp_pvc_name="${pvc_name}-ceph-temp"
    fi

    echo "  Current size: ${pvc_size}"
    echo "  Access mode:  ${pvc_access_mode}"
    if $AUTO_SWAP; then
        echo "  Temp PVC:     ${temp_pvc_name} (will be swapped back to ${pvc_name})"
    else
        echo "  Dest PVC:     ${temp_pvc_name}"
    fi
    echo ""

    # Confirm migration
    log_info "Ready to migrate PVC '${pvc_name}' from ${current_sc} to ${dest_storage_class}"
    echo ""
    echo "Migration steps:"
    echo "  1. Create temp destination PVC '${temp_pvc_name}' with ${dest_storage_class}"
    if [[ "$current_sc" == "$SOURCE_SC" ]]; then
        echo "  2. Migrate data using pv-migrate (rsync) - scheduled on ${ZFS_NODE}"
    else
        echo "  2. Migrate data using pv-migrate (rsync)"
    fi
    if $AUTO_SWAP; then
        echo "  3. Swap PVCs to restore original name '${pvc_name}'"
    else
        echo "  3. Manual: Swap PVCs or update app to use new name"
    fi
    echo ""
    # Check for pods using this PVC and offer to scale them down
    local pods_using_pvc
    pods_using_pvc=$(kubectl get pods -n "$namespace" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.volumes[*].persistentVolumeClaim.claimName}{"\n"}{end}' 2>/dev/null | grep "\b${pvc_name}\b" | awk '{print $1}' || true)

    local workloads_to_scale=()
    local workloads_original_replicas=()
    local flux_kustomization_to_suspend=""

    if [[ -n "$pods_using_pvc" ]]; then
        log_warning "Found pods using this PVC: $pods_using_pvc"
        echo ""

        # Collect workloads and their current replica counts
        for pod in $pods_using_pvc; do
            local owner_kind owner_name
            owner_kind=$(kubectl get pod -n "$namespace" "$pod" -o jsonpath='{.metadata.ownerReferences[0].kind}' 2>/dev/null || echo "Unknown")
            owner_name=$(kubectl get pod -n "$namespace" "$pod" -o jsonpath='{.metadata.ownerReferences[0].name}' 2>/dev/null || echo "Unknown")

            local workload_type="" workload_name="" replicas=""

            if [[ "$owner_kind" == "ReplicaSet" ]]; then
                local deployment
                deployment=$(kubectl get replicaset -n "$namespace" "$owner_name" -o jsonpath='{.metadata.ownerReferences[0].name}' 2>/dev/null || echo "")
                if [[ -n "$deployment" ]]; then
                    workload_type="deployment"
                    workload_name="$deployment"
                    replicas=$(kubectl get deployment -n "$namespace" "$deployment" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
                fi
            elif [[ "$owner_kind" == "StatefulSet" ]]; then
                workload_type="statefulset"
                workload_name="$owner_name"
                replicas=$(kubectl get statefulset -n "$namespace" "$owner_name" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")

                # Check if StatefulSet is managed by an operator (has ownerReferences)
                local sts_owner
                sts_owner=$(kubectl get statefulset -n "$namespace" "$owner_name" -o jsonpath='{.metadata.ownerReferences[0].kind}' 2>/dev/null || echo "")
                if [[ -n "$sts_owner" ]]; then
                    log_warning "  StatefulSet '$owner_name' is managed by operator: $sts_owner"
                    log_warning "  Operator-managed StatefulSets will fight scale-down commands!"
                    log_warning "  We need to suspend the Flux Kustomization first to stop reconciliation"

                    # The Flux Kustomization naming convention is: {namespace}_{app-name}
                    # StatefulSet names may not match app names exactly (e.g., prometheus-kube-prometheus-stack vs kube-prometheus-stack)
                    # Search cluster-apps inventory for the matching Kustomization
                    local sts_base="${owner_name%%-[0-9]*}"  # Remove trailing -0, -1, etc
                    local kustomization=""

                    # Search cluster-apps inventory for Kustomizations matching this namespace and StatefulSet
                    # Inventory format: namespace_appname_kustomize.toolkit.fluxcd.io_Kustomization
                    # Try multiple app name patterns
                    local app_name1="$sts_base"                                    # Pattern 1: exact match
                    local app_name2="${sts_base#prometheus-}"                      # Pattern 2: remove prometheus- prefix
                    local app_name3="${sts_base#alertmanager-}"                    # Pattern 3: remove alertmanager- prefix

                    # Try direct kubectl get first (fastest)
                    for app_name in "$app_name1" "$app_name2" "$app_name3"; do
                        kustomization="${namespace}_${app_name}"
                        if kubectl get kustomization -n flux-system "$kustomization" &>/dev/null 2>&1; then
                            flux_kustomization_to_suspend="$kustomization"
                            log_info "  Found Flux Kustomization: $kustomization"
                            break
                        fi
                    done

                    # If not found, search inventory
                    if [[ -z "$flux_kustomization_to_suspend" ]]; then
                        # Extract all Kustomization names from inventory that match namespace
                        local inventory_kustomizations
                        inventory_kustomizations=$(kubectl get kustomization -n flux-system cluster-apps -o json 2>/dev/null | \
                            jq -r --arg ns "$namespace" \
                            '.status.inventory.entries[]?.id // empty |
                             select(contains("kustomize.toolkit.fluxcd.io") and startswith($ns + "_")) |
                             split("_") | .[0] + "_" + .[1]' 2>/dev/null | sort -u || echo "")

                        # Try each inventory entry
                        while IFS= read -r inv_kustomization; do
                            [[ -z "$inv_kustomization" ]] && continue
                            if kubectl get kustomization -n flux-system "$inv_kustomization" &>/dev/null 2>&1; then
                                # Check if it matches any of our app name patterns
                                for app_name in "$app_name1" "$app_name2" "$app_name3"; do
                                    if [[ "$inv_kustomization" == "${namespace}_${app_name}" ]] || \
                                       [[ "$inv_kustomization" == *"${app_name}"* ]]; then
                                        flux_kustomization_to_suspend="$inv_kustomization"
                                        log_info "  Found Flux Kustomization from inventory: $inv_kustomization"
                                        break 2
                                    fi
                                done
                            fi
                        done <<< "$inventory_kustomizations"
                    fi

                    if [[ -z "$flux_kustomization_to_suspend" ]]; then
                        log_warning "  Could not find Flux Kustomization automatically"
                        log_warning "  For operator-managed StatefulSets, migration will proceed with --ignore-mounted"
                        log_warning "  If scale-down fails, the operator will reconcile it back"
                    fi
                fi
            elif [[ "$owner_kind" == "Job" || "$owner_kind" == "CronJob" ]]; then
                # Jobs/CronJobs - skip scaling, they'll be deleted/completed
                log_info "  $pod is a $owner_kind pod - will not scale"
                continue
            else
                log_warning "  $pod has unknown owner: $owner_kind/$owner_name"
                continue
            fi

            # Add to list if not already present
            local already_added=false
            for existing_workload in "${workloads_to_scale[@]}"; do
                if [[ "$existing_workload" == "${workload_type}/${workload_name}" ]]; then
                    already_added=true
                    break
                fi
            done

            if [[ "$already_added" == false && -n "$workload_name" ]]; then
                workloads_to_scale+=("${workload_type}/${workload_name}")
                workloads_original_replicas+=("$replicas")
                log_info "  Will scale: ${workload_type}/${workload_name} (current replicas: ${replicas})"
            fi
        done

        echo ""

        if [[ ${#workloads_to_scale[@]} -gt 0 ]]; then
            # Check if we have operator-managed workloads but couldn't find Kustomization
            local needs_suspension=false
            if [[ -n "$flux_kustomization_to_suspend" ]]; then
                needs_suspension=true
            fi

            if confirm "Scale down these workloads before migration?"; then
                # Suspend Flux Kustomization if needed (for operator-managed workloads)
                if [[ -n "$flux_kustomization_to_suspend" ]]; then
                    log_step "Suspending Flux Kustomization..."
                    if $DRY_RUN; then
                        log_info "[DRY-RUN] Would suspend: flux suspend kustomization -n flux-system ${flux_kustomization_to_suspend}"
                    else
                        log_info "  Suspending Kustomization: ${flux_kustomization_to_suspend}"
                        kubectl patch kustomization -n flux-system "$flux_kustomization_to_suspend" --type=merge -p '{"spec":{"suspend":true}}'  2>/dev/null || {
                            log_warning "Failed to suspend Kustomization using kubectl, trying flux CLI..."
                            flux suspend kustomization -n flux-system "$flux_kustomization_to_suspend" || {
                                log_error "Failed to suspend Flux Kustomization"
                                log_warning "Cannot scale down operator-managed workloads without suspending Kustomization"
                                log_warning "Migration will proceed with --ignore-mounted (pods can remain running)"
                                log_warning "Skipping scale-down to avoid operator reconciliation conflicts"
                                needs_suspension=false
                            }
                        }
                        if [[ "$needs_suspension" == true ]]; then
                            log_success "Kustomization suspended"
                            # Give Flux a moment to stop reconciling
                            sleep 3
                        fi
                    fi
                fi

                # Only scale down if:
                # 1. We don't need suspension (not operator-managed), OR
                # 2. We successfully suspended the Kustomization
                local should_scale=true
                if [[ -n "$flux_kustomization_to_suspend" ]] && [[ "$needs_suspension" == false ]]; then
                    should_scale=false
                    log_warning "Skipping scale-down - Kustomization suspension failed"
                    log_info "Migration will proceed with --ignore-mounted (pods can remain running)"
                fi

                if [[ "$should_scale" == true ]]; then

                log_step "Scaling down workloads..."
                for i in "${!workloads_to_scale[@]}"; do
                    local workload="${workloads_to_scale[$i]}"
                    local workload_type=$(echo "$workload" | cut -d'/' -f1)
                    local workload_name=$(echo "$workload" | cut -d'/' -f2)

                    if $DRY_RUN; then
                        log_info "[DRY-RUN] Would scale: kubectl scale ${workload_type} -n ${namespace} ${workload_name} --replicas=0"
                    else
                        log_info "  Scaling down ${workload_type}/${workload_name}..."
                        kubectl scale "${workload_type}" -n "$namespace" "$workload_name" --replicas=0
                    fi
                done

                if ! $DRY_RUN; then
                    log_info "Waiting for pods to terminate..."
                    # Give Kubernetes a moment to process the scale-down command
                    sleep 5

                    # For StatefulSets, we need to wait for pods to be fully deleted (not just terminated)
                    # StatefulSets delete pods in reverse order, and each must terminate before the next
                    local has_statefulset=false
                    for workload in "${workloads_to_scale[@]}"; do
                        local workload_type=$(echo "$workload" | cut -d'/' -f1)
                        if [[ "$workload_type" == "statefulset" ]]; then
                            has_statefulset=true
                            break
                        fi
                    done

                    local wait_count=0
                    local max_wait=180  # 3 minutes for StatefulSets (they can take longer)
                    local running_pods existing_pods

                    while [[ $wait_count -lt $max_wait ]]; do
                        # Get pods using this PVC that are actually Running (not Terminating)
                        running_pods=$(kubectl get pods -n "$namespace" -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.name}{" "}{.spec.volumes[*].persistentVolumeClaim.claimName}{"\n"}{end}' 2>/dev/null | grep "\b${pvc_name}\b" | awk '{print $1}' || true)

                        if [[ -z "$running_pods" ]]; then
                            # For StatefulSets, also check that pods are actually deleted (not just terminating)
                            if [[ "$has_statefulset" == true ]]; then
                                existing_pods=$(kubectl get pods -n "$namespace" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.volumes[*].persistentVolumeClaim.claimName}{"\n"}{end}' 2>/dev/null | grep "\b${pvc_name}\b" | awk '{print $1}' || true)
                                if [[ -z "$existing_pods" ]]; then
                                    log_success "All pods using this PVC have been deleted"
                                    break
                                else
                                    # Pods exist but are not running - they're terminating
                                    if [[ $((wait_count % 10)) -eq 0 ]]; then
                                        log_info "  Pods are terminating... (${wait_count}s/${max_wait}s)"
                                        log_info "  Terminating pods: $existing_pods"
                                    fi
                                fi
                            else
                                # For Deployments, just check running pods is enough
                                log_success "All pods using this PVC have terminated"
                                break
                            fi
                        else
                            if [[ $((wait_count % 10)) -eq 0 ]]; then
                                log_info "  Still waiting for pods to terminate... (${wait_count}s/${max_wait}s)"
                                log_info "  Running pods: $running_pods"
                            fi
                        fi

                        sleep 2
                        ((wait_count++))
                    done

                    if [[ $wait_count -ge $max_wait ]]; then
                        if [[ -n "$running_pods" ]]; then
                            log_warning "Pods did not terminate within ${max_wait}s, but continuing..."
                            log_warning "Remaining running pods: $running_pods"
                        elif [[ "$has_statefulset" == true ]] && [[ -n "$existing_pods" ]]; then
                            log_warning "Pods are still terminating after ${max_wait}s, but continuing..."
                            log_warning "Terminating pods: $existing_pods"
                            log_info "StatefulSet pods can take time to fully delete - migration may proceed"
                        fi
                    fi
                fi

                log_success "Workloads scaled down"
                echo ""
                else
                    log_info "Skipping scale-down - proceeding with migration using --ignore-mounted"
                    echo ""
                fi

                # Store info for later scale-up
                if [[ ${#workloads_to_scale[@]} -gt 0 ]]; then
                    echo -e "${YELLOW}Remember to scale back up after migration:${NC}"

                    # If we suspended a Flux Kustomization, remind to resume it first
                    if [[ -n "$flux_kustomization_to_suspend" ]]; then
                        echo "  # Resume Flux Kustomization first:"
                        echo "  flux resume kustomization -n flux-system ${flux_kustomization_to_suspend}"
                        echo ""
                        echo "  # Then scale up workloads:"
                    fi

                    for i in "${!workloads_to_scale[@]}"; do
                        local workload="${workloads_to_scale[$i]}"
                        local replicas="${workloads_original_replicas[$i]}"
                        local workload_type=$(echo "$workload" | cut -d'/' -f1)
                        local workload_name=$(echo "$workload" | cut -d'/' -f2)
                        echo "  kubectl scale ${workload_type} -n ${namespace} ${workload_name} --replicas=${replicas}"
                    done
                    echo ""
                fi
            else
                log_warning "Continuing with pods running - migration may fail!"
                echo ""
            fi
        else
            log_info "No scalable workloads found (jobs/cronjobs will be ignored)"
            echo ""
        fi
    else
        log_success "No pods currently using this PVC"
        echo ""
    fi

    if ! confirm "Continue with migration?"; then
        log_warning "Skipped ${pvc_name} migration"
        echo ""
        return 0
    fi

    # Check if temp PVC already exists
    if kubectl get pvc -n "$namespace" "$temp_pvc_name" &>/dev/null; then
        local existing_sc
        existing_sc=$(get_pvc_storage_class "$namespace" "$temp_pvc_name")
        if [[ "$existing_sc" == "$dest_storage_class" ]]; then
            log_info "Temp PVC '${temp_pvc_name}' already exists with ${dest_storage_class}"
        else
            log_error "Temp PVC '${temp_pvc_name}' exists but uses '${existing_sc}' (expected ${dest_storage_class})"
            return 1
        fi
    else
        # Step 1: Create temp destination PVC
        log_step "Step 1: Creating temp destination PVC '${temp_pvc_name}'..."

        if $DRY_RUN; then
            log_info "[DRY-RUN] Would create PVC '${temp_pvc_name}' with:"
            echo "    storageClassName: ${dest_storage_class}"
            echo "    storage: ${pvc_size}"
            echo "    accessModes: [${pvc_access_mode}]"
        else
            cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${temp_pvc_name}
  namespace: ${namespace}
spec:
  accessModes:
    - ${pvc_access_mode}
  storageClassName: ${dest_storage_class}
  resources:
    requests:
      storage: ${pvc_size}
EOF
            log_success "Temp destination PVC created"
        fi
    fi

    # Wait for temp PVC to be bound
    if ! $DRY_RUN; then
        log_step "  Waiting for temp PVC to be bound..."
        if ! kubectl wait --for=jsonpath='{.status.phase}'=Bound \
            -n "$namespace" "pvc/${temp_pvc_name}" --timeout=5m; then
            log_error "Temp PVC did not become bound in time"
            return 1
        fi
        log_success "Temp PVC is bound"
    fi

    # Step 2: Perform migration with node selector for ZFS (only if source is ZFS)
    log_step "Step 2: Migrating data from '${pvc_name}' to '${temp_pvc_name}'..."

    # Build pv-migrate command
    local migrate_cmd=(
        kubectl pv-migrate
        --source-namespace "$namespace"
        --source "$pvc_name"
        --dest-namespace "$namespace"
        --dest "$temp_pvc_name"
        --ignore-mounted
        --helm-timeout 5m
    )

    # Only add node selector if source is ZFS (needs to be on control-1)
    if [[ "$current_sc" == "$SOURCE_SC" ]]; then
        log_info "Scheduling rsync pods on ${ZFS_NODE} for ZFS compatibility"
        migrate_cmd+=(
            --helm-set "rsync.nodeSelector.kubernetes\.io/hostname=${ZFS_NODE}"
            --helm-set "sshd.nodeSelector.kubernetes\.io/hostname=${ZFS_NODE}"
        )
    fi

    if $DRY_RUN; then
        log_info "[DRY-RUN] Would run: ${migrate_cmd[*]}"
    else
        if "${migrate_cmd[@]}"; then
            log_success "Data migration completed successfully"
        else
            log_error "Data migration failed"
            log_error "Check the error above. Temp PVC '${temp_pvc_name}' may need cleanup."
            if ! confirm "Continue with next migration?"; then
                log_warning "Exiting due to migration failure"
                exit 1
            fi
            return 1
        fi
    fi

    # Step 3: Swap PVCs if auto-swap is enabled
    if $AUTO_SWAP; then
        echo ""
        log_step "Step 3: Swapping PVCs to keep original name..."
        if swap_pvcs "$namespace" "$pvc_name" "$temp_pvc_name" "$pvc_size" "$pvc_access_mode" "$dest_storage_class"; then
            log_success "Migration complete! PVC '${pvc_name}' is now using ${dest_storage_class}"
        else
            log_error "PVC swap failed. Manual intervention required."
            log_info "Temp PVC '${temp_pvc_name}' contains your data."
            return 1
        fi
    else
        echo ""
        log_success "Data migration of '${pvc_name}' completed!"
        echo ""
        echo -e "${YELLOW}To complete migration and keep the original name '${pvc_name}':${NC}"
        echo ""
        echo "  1. Scale down your application"
        echo "  2. Run the PVC swap commands:"
        echo ""
        echo "     # Get PV name"
        echo "     PV_NAME=\$(kubectl get pvc -n ${namespace} ${temp_pvc_name} -o jsonpath='{.spec.volumeName}')"
        echo ""
        echo "     # Set PV reclaim policy to Retain"
        echo "     kubectl patch pv \$PV_NAME -p '{\"spec\":{\"persistentVolumeReclaimPolicy\":\"Retain\"}}'"
        echo ""
        echo "     # Delete old source PVC"
        echo "     kubectl delete pvc -n ${namespace} ${pvc_name}"
        echo ""
        echo "     # Delete temp PVC (PV remains due to Retain)"
        echo "     kubectl delete pvc -n ${namespace} ${temp_pvc_name}"
        echo ""
        echo "     # Remove claimRef from PV"
        echo "     kubectl patch pv \$PV_NAME --type json -p '[{\"op\": \"remove\", \"path\": \"/spec/claimRef\"}]'"
        echo ""
        echo "     # Create new PVC with original name"
        echo "     kubectl apply -f - <<EOF"
        echo "apiVersion: v1"
        echo "kind: PersistentVolumeClaim"
        echo "metadata:"
        echo "  name: ${pvc_name}"
        echo "  namespace: ${namespace}"
        echo "spec:"
        echo "  accessModes: [${pvc_access_mode}]"
        echo "  storageClassName: ${dest_storage_class}"
        echo "  resources:"
        echo "    requests:"
        echo "      storage: ${pvc_size}"
        echo "  volumeName: \$PV_NAME"
        echo "EOF"
        echo ""
        echo "  3. Scale up your application"
        echo ""
        echo -e "  ${CYAN}Or re-run with --swap to do this automatically${NC}"
    fi
    echo ""
}

# Main
check_dependencies

echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     ZFS to Ceph PVC Migration Script                      ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Migration: ${CYAN}${SOURCE_SC}${NC} → ${CYAN}${DEST_SC}${NC} (or per-PVC override)"
echo -e "ZFS Node:  ${CYAN}${ZFS_NODE}${NC} (rsync pods will be scheduled here)"
$DRY_RUN && echo -e "${YELLOW}Running in DRY-RUN mode - no changes will be made${NC}"
$AUTO_SWAP && echo -e "${CYAN}AUTO-SWAP enabled - PVCs will keep their original names${NC}"
echo -e "${BLUE}Note: Volsync-backed PVCs will be automatically skipped${NC}"
echo ""

# Clean up any stuck pv-migrate resources first
cleanup_stuck_migrations

# Ensure ZFS driver is available before starting migrations
ensure_zfs_driver

# Migration list
# kube-prometheus-stack PVCs
migrate_pvc "observability" "prometheus-kube-prometheus-stack-db-prometheus-kube-prometheus-stack-0" "prometheus PVC (observability namespace)" "ceph-block"
migrate_pvc "observability" "alertmanager-kube-prometheus-stack-db-alertmanager-kube-prometheus-stack-0" "alertmanager PVC 0 (observability namespace)" "ceph-block"
migrate_pvc "observability" "alertmanager-kube-prometheus-stack-db-alertmanager-kube-prometheus-stack-1" "alertmanager PVC 1 (observability namespace)" "ceph-block"
# Other observability PVCs
migrate_pvc "observability" "config-gatus-0" "gatus PVC (observability namespace)" "ceph-block"

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}All migrations completed!${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
if $AUTO_SWAP; then
    echo -e "${BLUE}Summary:${NC}"
    echo "  - PVCs have been migrated and swapped to keep original names"
    echo "  - Your applications should work with the same PVC names"
    echo "  - Update your GitOps manifests to use storageClassName: ${DEST_SC}"
else
    echo -e "${BLUE}Next steps:${NC}"
    echo "  1. Run the swap commands shown above for each PVC (or re-run with --swap)"
    echo "  2. Update your GitOps manifests to use storageClassName: ${DEST_SC}"
    echo "  3. Verify applications work correctly"
fi
echo ""
