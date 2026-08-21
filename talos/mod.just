set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

# Values are assigned and checked before use throughout this file. Process substitutions and
# argument-position command substitutions discard their producer's exit status, and some tools
# exit 0 with empty output; see .agents/common-operations.md.

# Cluster-wide addresses, defined once: the VIP is referenced from every layer and from
# talosconfig, and the retired talconfig.yaml used a YAML anchor for the same reason.
vip := "192.168.0.2"
gateway := "192.168.0.1"

[doc('Render a node full machine config to stdout')]
render-config node:
    role="$(just talos node-role "{{ node }}")"
    [[ -n "${role}" ]] || { echo "empty role for {{ node }}" >&2; exit 1; }
    # One context for every layer, so any layer may reference any of these.
    ctx=(-D "vip={{ vip }}" -D "gateway={{ gateway }}")
    # Rendered to variables before talosctl runs, so a failed layer aborts before a single byte
    # reaches stdout. Inline <(just template) dropped the layer silently and still emitted a
    # config, which apply-node pipes straight to a node.
    cluster_layer="$(just template "{{ source_directory() }}/cluster.yaml.j2" "${ctx[@]}")"
    role_layer="$(just template "{{ source_directory() }}/${role}.yaml.j2" "${ctx[@]}")"
    node_layer="$(just template "{{ source_directory() }}/nodes/${role}/{{ node }}.yaml.j2" "${ctx[@]}")"
    talosctl machineconfig patch \
        <(printf '%s' "${cluster_layer}") \
        -p @<(printf '%s' "${role_layer}") \
        -p @<(printf '%s' "${node_layer}")

[doc('Generate the Talos client config from the secrets bundle')]
talosconfig:
    out="{{ source_directory() }}/talosconfig"
    talosctl gen config kubernetes https://{{ vip }}:6443 --force \
        --with-secrets <(just talsecret) \
        --output-types talosconfig --output "${out}"
    endpoints="$(just talos node-ips controlplane)"
    nodes="$(just talos node-ips)"
    talosctl --talosconfig "${out}" config endpoint ${endpoints}
    talosctl --talosconfig "${out}" config node ${nodes}

[doc('Diff a node rendered config against what the node is running')]
diff-node node ip:
    just talos render-config "{{ node }}" | talosctl -n "{{ ip }}" apply-config -f /dev/stdin --dry-run

[confirm('Apply Talos config to the node. Continue? [y|N]')]
[doc('Apply Talos config to a node')]
apply-node node ip *args:
    just talos render-config "{{ node }}" | talosctl -n "{{ ip }}" apply-config -f /dev/stdin {{ args }}

[confirm('Upgrade Talos on the node. Continue? [y|N]')]
[doc('Upgrade Talos on a single node')]
upgrade-node node ip:
    # Assigned: inlined, an empty image would reach `upgrade -i ""` and powercycle the node anyway.
    image="$(just talos machine-image "{{ node }}")"
    talosctl -n "{{ ip }}" upgrade -i "${image}" -m powercycle --timeout=15m

[doc('Upgrade Kubernetes')]
upgrade-k8s:
    version="$(just tuppr-version kubernetes)"
    talosctl upgrade-k8s --to "${version}"

# wipe=false skips the STATE/EPHEMERAL wipe, leaving the installed system on disk.
[confirm('This will reset the node to maintenance mode. Continue? [y|N]')]
[doc('Reset a node back to maintenance mode')]
reset-node ip wipe='true':
    talosctl -n "{{ ip }}" reset --reboot --graceful=false --wait=false \
        {{ if wipe == 'true' { '--system-labels-to-wipe STATE --system-labels-to-wipe EPHEMERAL' } else { '' } }}

[doc('Download a Talos ISO from the Image Factory for a node')]
download-image node version:
    schematic="$(just talos schematic-id "{{ node }}")"
    curl -fL --remove-on-error --retry 5 --retry-delay 5 --retry-all-errors --progress-bar \
        -o "{{ source_directory() }}/talos-{{ version }}-${schematic:0:8}.iso" \
        "https://factory.talos.dev/image/${schematic}/{{ version }}/metal-amd64.iso"

# Directory placement is the single source of truth for a node's role; machine.type is
# set by the role layer, so a node cannot claim one role by filename and another by content.
[private]
node-role node:
    for role in controlplane workers; do
        if [[ -f "{{ source_directory() }}/nodes/${role}/{{ node }}.yaml.j2" ]]; then
            echo "${role}"; exit 0
        fi
    done
    echo "no node file found for {{ node }}" >&2; exit 1

# One address per node file, so a grep is enough; render and parse if that stops holding.
# Defaults to every role; talosconfig passes `controlplane` because endpoints must be
# control-plane nodes and a worker file would otherwise be injected into the endpoint list.
[private]
node-ips role="*":
    ips="$(grep -hoP '^\s+- address:\s*\K[0-9.]+' "{{ source_directory() }}"/nodes/{{ role }}/*.yaml.j2 | sort -u | tr '\n' ' ')"
    # An empty result means the grep stopped matching (indentation or key rename), not that the
    # cluster has no nodes. Fail rather than hand talosctl an empty endpoint list.
    [[ -n "${ips// /}" ]] || { echo "no addresses matched under nodes/{{ role }}/" >&2; exit 1; }
    echo "${ips}"

# Per-node override wins when present, otherwise the shared fleet schematic applies.
[private]
schematic-file node:
    for f in "{{ source_directory() }}"/nodes/*/"{{ node }}".schematic.yaml; do
        [[ -f "$f" ]] && { echo "$f"; exit 0; }
    done
    echo "{{ source_directory() }}/schematic.yaml"

# Off the render path since every node pins its own installer: `download-image` is the only caller.
# Schematics are plain YAML, not templates: POST the file directly rather than routing it through
# `template`, which would decrypt the secrets bundle to render a file that references no secrets.
# Bounded so `download-image` fails rather than hanging on a stalled factory. Retries are safe:
# the endpoint is content-addressed, so re-POSTing identical content returns the same id.
[private]
schematic-id node:
    file="$(just talos schematic-file "{{ node }}")"
    [[ -f "${file}" ]] || { echo "no schematic file for {{ node }}: ${file}" >&2; exit 1; }
    curl -sfX POST --connect-timeout 5 --max-time 30 --retry 3 --retry-max-time 60 \
        --data-binary @"${file}" \
        https://factory.talos.dev/schematics \
        | jq -er '.id | strings | select(length > 0)'

[private]
machine-image node:
    # Read back from the render rather than rebuilt from schematic+version: the field moves
    # document under UnattendedInstallConfig, and this value becomes `talosctl upgrade -i`.
    image="$(just talos render-config "{{ node }}" | yq -er 'select(.machine.install != null) | .machine.install.image')"
    [[ -n "${image}" ]] || { echo "no install image in rendered config for {{ node }}" >&2; exit 1; }
    echo "${image}"

[doc('Build the custom-kernel Talos installer for the given nodes (docker/talos-kernel/README.md)')]
kernel-build +nodes:
    "{{ justfile_directory() }}/scripts/talos-kernel-build.sh" \
        "$(just tuppr-version talos)" {{ nodes }}
