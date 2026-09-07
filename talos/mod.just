set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

# Values are assigned and checked before use throughout this file. Process substitutions and
# argument-position command substitutions discard their producer's exit status, and some tools
# exit 0 with empty output; see .agents/common-operations.md.

# Every recipe runs against talos/topf.yaml, which is both the node inventory and the version
# pin. topf reads talsecret.sops.yaml itself, so nothing here decrypts anything.
export TOPFCONFIG := source_directory() / "topf.yaml"

[doc('Render every node machine config to talos/rendered (gitignored)')]
render *args:
    topf render --output "{{ source_directory() }}/rendered" {{ args }}

[doc('Show what would change on the nodes, without applying')]
diff *args:
    # Exits 2 when there IS a diff, which is the normal answer here, not a failure.
    topf apply --dry-run {{ args }} || [[ $? -eq 2 ]]

[confirm('Apply Talos config to the cluster. Continue? [y|N]')]
[doc('Apply Talos config; topf shows a per-node diff and asks again')]
apply *args:
    topf apply {{ args }}

[doc('List nodes and their live state')]
nodes:
    topf nodes

[doc('Generate the Talos client config from the secrets bundle')]
talosconfig:
    topf talosconfig > "{{ source_directory() }}/talosconfig"

[doc('Upgrade Kubernetes to the version in the tuppr CR')]
upgrade-k8s:
    version="$(just tuppr-version kubernetes)"
    talosctl upgrade-k8s --to "${version}"

# --full=false is the closest thing to the old wipe=false and is still MORE destructive: it
# wipes STATE and EPHEMERAL. topf has no mode that leaves the installed system on disk, and its
# own default (--full) takes the whole disk.
[confirm('This wipes STATE and EPHEMERAL on the node. Continue? [y|N]')]
[doc('Reset a node back to maintenance mode')]
reset-node node:
    topf reset --full=false --confirm=false --nodes-filter "^{{ node }}$"

[doc('Download a Talos ISO from the Image Factory for a node')]
download-image node version:
    schematic="$(just talos schematic-id "{{ node }}")"
    curl -fL --remove-on-error --retry 5 --retry-delay 5 --retry-all-errors --progress-bar \
        -o "{{ source_directory() }}/talos-{{ version }}-${schematic:0:8}.iso" \
        "https://factory.talos.dev/image/${schematic}/{{ version }}/metal-amd64.iso"

# Renovate keeps the two files in step because the annotations match; this catches the hand
# edit that moves only one. Cheap enough to run in CI on every talos/ PR.
[doc('Assert topf.yaml and the tuppr CRs name the same versions')]
check-versions:
    # `v1.14.0-k7.1.13` -> the composite is the installer tag, its prefix the Talos release.
    talos="$(just tuppr-version talos)"
    rc=0
    same() {
        local got; got="$(yq -e "$2" "${TOPFCONFIG}")"
        [[ "${got}" == "$3" ]] || { echo "topf.yaml $1 = ${got}, tuppr CR says $3" >&2; rc=1; }
    }
    same kubernetesVersion .kubernetesVersion "$(just tuppr-version kubernetes)"
    same talosVersion .talosVersion "${talos%-k*}"
    same data.installerTag .data.installerTag "${talos}"
    ((rc)) || echo "ok: topf.yaml matches the tuppr CRs"
    exit "${rc}"

# The node files are the fleet inventory, so a caller cannot build against a stale hand-written
# list. Read from topf.yaml, the same file topf itself renders from.
[private]
nodes-list:
    names="$(yq -er '.nodes[].host' "${TOPFCONFIG}" | sort | tr '\n' ' ')"
    [[ -n "${names// /}" ]] || { echo "no nodes in ${TOPFCONFIG}" >&2; exit 1; }
    echo "${names}"

# Per-node override wins when present, otherwise the shared fleet schematic applies. The `@`
# prefix is topf's "resolve this path" marker; paths are relative to topf.yaml.
[private]
schematic-file node:
    ref="$(yq -er '(.nodes[] | select(.host == "{{ node }}") | .schematicId) // .schematicId // ""' \
        "${TOPFCONFIG}")"
    [[ -n "${ref}" ]] || { echo "no schematicId for {{ node }}" >&2; exit 1; }
    echo "{{ source_directory() }}/${ref#@}"

# Still a Factory POST rather than `topf schematic-ids`: that command ignores --nodes-filter at
# v0.6.0 and prints every node's id, and it computes ids locally without REGISTERING them, which
# the ISO URL below needs. Bounded so `download-image` fails rather than hanging on a stalled
# factory. Retries are safe: the endpoint is content-addressed, so re-POSTing identical content
# returns the same id.
[private]
schematic-id node:
    file="$(just talos schematic-file "{{ node }}")"
    [[ -f "${file}" ]] || { echo "no schematic file for {{ node }}: ${file}" >&2; exit 1; }
    curl -sfX POST --connect-timeout 5 --max-time 30 --retry 3 --retry-max-time 60 \
        --data-binary @"${file}" \
        https://factory.talos.dev/schematics \
        | jq -er '.id | strings | select(length > 0)'

[doc('Build the custom-kernel Talos installers; defaults to every node (docker/talos-kernel/README.md)')]
kernel-build *nodes:
    names="{{ nodes }}"
    [[ -n "${names}" ]] || names="$(just talos nodes-list)"
    "{{ justfile_directory() }}/scripts/talos-kernel-build.sh" \
        "$(just tuppr-version talos)" ${names}
