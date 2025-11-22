#!/bin/bash
# Quick script to check VolSync/Kopia repository stats

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_header() {
    echo -e "${CYAN}=== $1 ===${NC}"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC}  $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC}  $1"
}

# Function to convert size to bytes
size_to_bytes() {
    local size=$1
    if [[ $size =~ ^([0-9.]+)Ti$ ]]; then
        echo "$(echo "${BASH_REMATCH[1]} * 1099511627776" | bc | cut -d. -f1)"
    elif [[ $size =~ ^([0-9.]+)Gi$ ]]; then
        echo "$(echo "${BASH_REMATCH[1]} * 1073741824" | bc | cut -d. -f1)"
    elif [[ $size =~ ^([0-9.]+)Mi$ ]]; then
        echo "$(echo "${BASH_REMATCH[1]} * 1048576" | bc | cut -d. -f1)"
    elif [[ $size =~ ^([0-9.]+)Ki$ ]]; then
        echo "$(echo "${BASH_REMATCH[1]} * 1024" | bc | cut -d. -f1)"
    elif [[ $size =~ ^([0-9.]+)G$ ]]; then
        echo "$(echo "${BASH_REMATCH[1]} * 1000000000" | bc | cut -d. -f1)"
    elif [[ $size =~ ^([0-9.]+)M$ ]]; then
        echo "$(echo "${BASH_REMATCH[1]} * 1000000" | bc | cut -d. -f1)"
    elif [[ $size =~ ^([0-9.]+)K$ ]]; then
        echo "$(echo "${BASH_REMATCH[1]} * 1000" | bc | cut -d. -f1)"
    else
        echo "$size"
    fi
}

# Function to format bytes to human readable
format_bytes() {
    local bytes=$1
    if [ -z "$bytes" ] || [ "$bytes" = "0" ]; then
        echo "0B"
        return
    fi

    if [ "$bytes" -ge 1099511627776 ]; then
        echo "$(echo "scale=2; $bytes / 1099511627776" | bc)Ti"
    elif [ "$bytes" -ge 1073741824 ]; then
        echo "$(echo "scale=2; $bytes / 1073741824" | bc)Gi"
    elif [ "$bytes" -ge 1048576 ]; then
        echo "$(echo "scale=2; $bytes / 1048576" | bc)Mi"
    elif [ "$bytes" -ge 1024 ]; then
        echo "$(echo "scale=2; $bytes / 1024" | bc)Ki"
    else
        echo "${bytes}B"
    fi
}

# Check dependencies
command -v kubectl >/dev/null 2>&1 || { print_error "kubectl is required but not installed. Aborting."; exit 1; }
command -v jq >/dev/null 2>&1 || { print_error "jq is required but not installed. Aborting."; exit 1; }
command -v bc >/dev/null 2>&1 || { print_error "bc is required but not installed. Aborting."; exit 1; }

print_header "VolSync Repository Statistics"
echo ""

# Get Kopia pod name
KOPIA_POD=$(kubectl get pods -n volsync-system -l app.kubernetes.io/name=kopia -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$KOPIA_POD" ]; then
    print_error "Kopia pod not found"
    exit 1
fi

print_success "Connected to Kopia pod: $KOPIA_POD"
echo ""

# Get repository size from NFS mount
print_header "Repository Statistics"
REPO_SIZE_BYTES=$(kubectl exec -n volsync-system "$KOPIA_POD" -- du -sb /repository 2>/dev/null | awk '{print $1}' || echo "0")

if [ -z "$REPO_SIZE_BYTES" ] || [ "$REPO_SIZE_BYTES" = "0" ]; then
    print_warning "Could not determine repository size"
    REPO_SIZE_BYTES=0
    REPO_SIZE_FORMATTED="Unknown"
else
    REPO_SIZE_FORMATTED=$(format_bytes "$REPO_SIZE_BYTES")
    echo -e "  📦 Repository Size: ${GREEN}$REPO_SIZE_FORMATTED${NC}"
fi

# Get ReplicationSource count and calculate total source sizes
echo ""
echo "  Calculating source volumes..."

# Get all source PVCs with their sizes from ReplicationSource resources
REPLICATION_SOURCES=$(kubectl get replicationsources --all-namespaces -o json 2>/dev/null | \
    jq -r '.items[] | "\(.metadata.namespace)|\(.spec.sourcePVC)"' || true)

TOTAL_SOURCE_BYTES=0
SOURCE_COUNT=0
SOURCE_DETAILS=()

if [ -n "$REPLICATION_SOURCES" ]; then
    while IFS='|' read -r namespace pvc_name; do
        if [ -n "$namespace" ] && [ -n "$pvc_name" ]; then
            # Get PVC size
            SIZE=$(kubectl get pvc "$pvc_name" -n "$namespace" -o jsonpath='{.spec.resources.requests.storage}' 2>/dev/null || echo "")
            if [ -n "$SIZE" ]; then
                SIZE_BYTES=$(size_to_bytes "$SIZE")
                if [ -n "$SIZE_BYTES" ] && [ "$SIZE_BYTES" != "0" ]; then
                    TOTAL_SOURCE_BYTES=$(echo "$TOTAL_SOURCE_BYTES + $SIZE_BYTES" | bc)
                    SOURCE_COUNT=$((SOURCE_COUNT + 1))
                    SOURCE_DETAILS+=("$namespace/$pvc_name: $SIZE")
                fi
            fi
        fi
    done <<< "$REPLICATION_SOURCES"
fi

# Get retention policy (assuming all use same policy: hourly: 24, daily: 7)
# This means: 24 hourly snapshots + 7 daily snapshots = up to 31 snapshots per source
RETENTION_HOURLY=24
RETENTION_DAILY=7
MAX_SNAPSHOTS_PER_SOURCE=$((RETENTION_HOURLY + RETENTION_DAILY))

if [ "$SOURCE_COUNT" -gt 0 ] && [ "$TOTAL_SOURCE_BYTES" -gt 0 ]; then
    TOTAL_SOURCE_FORMATTED=$(format_bytes "$TOTAL_SOURCE_BYTES")
    echo -e "  📈 Total Source Size: ${CYAN}$TOTAL_SOURCE_FORMATTED${NC} ($SOURCE_COUNT volumes)"

    # Calculate theoretical max size (all snapshots without deduplication)
    THEORETICAL_MAX=$(echo "$TOTAL_SOURCE_BYTES * $MAX_SNAPSHOTS_PER_SOURCE" | bc)
    THEORETICAL_MAX_FORMATTED=$(format_bytes "$THEORETICAL_MAX")

    if [ "$REPO_SIZE_BYTES" -gt 0 ] && [ "$TOTAL_SOURCE_BYTES" -gt 0 ]; then
        # Calculate compression/deduplication ratio
        RATIO=$(echo "scale=2; $REPO_SIZE_BYTES * 100 / $TOTAL_SOURCE_BYTES" | bc)
        SAVINGS=$(echo "$TOTAL_SOURCE_BYTES - $REPO_SIZE_BYTES" | bc)
        SAVINGS_FORMATTED=$(format_bytes "$SAVINGS")
        SAVINGS_PERCENT=$(echo "scale=1; 100 - $RATIO" | bc)

        # Calculate deduplication ratio (vs theoretical max)
        DEDUP_RATIO=$(echo "scale=2; $REPO_SIZE_BYTES * 100 / $THEORETICAL_MAX" | bc)
        DEDUP_SAVINGS=$(echo "$THEORETICAL_MAX - $REPO_SIZE_BYTES" | bc)
        DEDUP_SAVINGS_FORMATTED=$(format_bytes "$DEDUP_SAVINGS")
        DEDUP_SAVINGS_PERCENT=$(echo "scale=1; 100 - $DEDUP_RATIO" | bc)

        echo ""
        echo -e "  💾 Storage Efficiency:"
        echo -e "     Repository:        ${GREEN}$REPO_SIZE_FORMATTED${NC}"
        echo -e "     Single Snapshot:   ${CYAN}$TOTAL_SOURCE_FORMATTED${NC}"
        echo -e "     Theoretical Max:   ${YELLOW}$THEORETICAL_MAX_FORMATTED${NC} (${MAX_SNAPSHOTS_PER_SOURCE} snapshots × $SOURCE_COUNT sources)"
        echo ""
        echo -e "     Compression:       ${GREEN}$SAVINGS_FORMATTED${NC} saved (${SAVINGS_PERCENT}% reduction vs single snapshot)"
        echo -e "     Deduplication:     ${GREEN}$DEDUP_SAVINGS_FORMATTED${NC} saved (${DEDUP_SAVINGS_PERCENT}% reduction vs all snapshots)"
        echo -e "     Overall Ratio:     ${CYAN}${DEDUP_RATIO}%${NC} of theoretical maximum"

        echo ""
        print_info "Repository contains up to ${MAX_SNAPSHOTS_PER_SOURCE} snapshots per source (hourly: ${RETENTION_HOURLY}, daily: ${RETENTION_DAILY})"
        print_info "Actual deduplication is excellent - storing $SOURCE_COUNT sources with ${MAX_SNAPSHOTS_PER_SOURCE} snapshots each in just $REPO_SIZE_FORMATTED"
    fi
else
    print_warning "Could not calculate source volumes size"
fi

# Get sync status summary
echo ""
print_header "Sync Status Summary"

SYNC_DATA=$(kubectl get replicationsources --all-namespaces -o json 2>/dev/null | \
    jq -r '.items[] | .status.conditions[]? | select(.type=="Synchronizing") | .status' || echo "")

if [ -n "$SYNC_DATA" ]; then
    SYNCING_COUNT=$(echo "$SYNC_DATA" | grep -c "^True$" || echo "0")
    SYNCED_COUNT=$(echo "$SYNC_DATA" | grep -c "^False$" || echo "0")
    TOTAL_SOURCES=$(echo "$SYNC_DATA" | wc -l)

    echo -e "  Total Sources:     ${CYAN}$TOTAL_SOURCES${NC}"
    echo -e "  Currently Syncing: ${YELLOW}$SYNCING_COUNT${NC}"
    echo -e "  Up to Date:        ${GREEN}$SYNCED_COUNT${NC}"
fi

# Get last sync times
LAST_SYNC=$(kubectl get replicationsources --all-namespaces -o json 2>/dev/null | \
    jq -r '[.items[] | .status.lastSyncTime // "Never"] | sort | reverse | .[0]' || echo "Unknown")

if [ "$LAST_SYNC" != "Unknown" ] && [ "$LAST_SYNC" != "Never" ]; then
    echo -e "  Last Sync:         ${CYAN}$LAST_SYNC${NC}"
fi

# Try to get detailed stats from Kopia CLI if available
echo ""
print_header "Detailed Kopia Stats"
KOPIA_STATS=$(kubectl exec -n volsync-system "$KOPIA_POD" -- kopia repository stats 2>/dev/null || true)
if [ -n "$KOPIA_STATS" ]; then
    echo "$KOPIA_STATS"
else
    print_info "Kopia CLI not available in pod. Use Kopia Web UI for detailed stats."
fi

# Show ReplicationSource details
echo ""
print_header "ReplicationSource Status"
kubectl get replicationsources --all-namespaces -o wide 2>/dev/null | head -20 || print_warning "Could not fetch ReplicationSource status"

# Show ReplicationDestination details
echo ""
print_header "ReplicationDestination Status"
kubectl get replicationdestinations --all-namespaces -o wide 2>/dev/null | head -20 || print_warning "Could not fetch ReplicationDestination status"

# Show source PVC details
if [ ${#SOURCE_DETAILS[@]} -gt 0 ]; then
    echo ""
    print_header "Source Volume Sizes"
    for detail in "${SOURCE_DETAILS[@]}"; do
        echo "  $detail"
    done | head -20
fi

echo ""
print_info "Grafana Dashboard: https://grafana.tanguille.site/d/cdp5agkgn4yrkc"
