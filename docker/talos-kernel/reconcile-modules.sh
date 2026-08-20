#!/usr/bin/env bash
# Reconcile talos hack/modules-amd64.txt against what a kernel package actually built.
#
# talos/Dockerfile:687 runs `install -D` for every listed module and fails the build on the
# first one missing, so a kernel bump that moves or drops a module breaks the installer
# build with an opaque error. This rewrites moved paths, drops vanished modules, and prints
# every change — the printed list IS the per-bump review item, so read it.
set -euo pipefail

KERNEL_IMAGE="${1:?usage: reconcile-modules.sh <kernel-image> <modules-amd64.txt>}"
MODULES_TXT="${2:?usage: reconcile-modules.sh <kernel-image> <modules-amd64.txt>}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cid="$(docker create "${KERNEL_IMAGE}" /bin/true)"
docker export "$cid" | tar -C "$tmp" -xf - 2>/dev/null || true
docker rm -f "$cid" >/dev/null

release="$(basename "$(find "$tmp/usr/lib/modules" -mindepth 1 -maxdepth 1 -type d | head -1)")"
modroot="$tmp/usr/lib/modules/$release"
echo "reconciling against ${release}"

moved=0 dropped=0
while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    if [ -e "$modroot/$entry" ]; then
        printf '%s\n' "$entry"
        continue
    fi
    # Moved? Match on basename; a unique hit is a path change, not a removal.
    hits="$(cd "$modroot" && find . -name "$(basename "$entry")" -type f | sed 's|^\./||')"
    if [ "$(printf '%s\n' "$hits" | grep -c .)" = 1 ] && [ -n "$hits" ]; then
        echo "  MOVED   $entry -> $hits" >&2
        printf '%s\n' "$hits"
        moved=$((moved + 1))
    else
        echo "  DROPPED $entry (not built by this config)" >&2
        dropped=$((dropped + 1))
    fi
done < "$MODULES_TXT" > "$tmp/reconciled.txt"

mv "$tmp/reconciled.txt" "$MODULES_TXT"
echo "reconciled: ${moved} moved, ${dropped} dropped"
