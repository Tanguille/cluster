set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

[doc('Render a node full machine config to stdout')]
render-config node:
    schematic="$(just talos schematic-id "{{ node }}")"
    role="$(just talos node-role "{{ node }}")"
    talosctl machineconfig patch \
        <(just template "{{ source_directory() }}/cluster.yaml.j2" -D "schematic=${schematic}") \
        -p @<(just template "{{ source_directory() }}/${role}.yaml.j2" -D "schematic=${schematic}") \
        -p @<(just template "{{ source_directory() }}/nodes/${role}/{{ node }}.yaml.j2" -D "schematic=${schematic}")

[doc('Generate the Talos client config from the secrets bundle')]
talosconfig:
    out="{{ source_directory() }}/talosconfig"
    talosctl gen config kubernetes https://192.168.0.2:6443 --force \
        --with-secrets <(just talsecret) \
        --output-types talosconfig --output "${out}"
    ips="$(just talos node-ips)"
    talosctl --talosconfig "${out}" config endpoint ${ips}
    talosctl --talosconfig "${out}" config node ${ips}

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
    talosctl -n "{{ ip }}" upgrade -i "$(just talos machine-image "{{ node }}")" -m powercycle --timeout=15m

[doc('Upgrade Kubernetes')]
upgrade-k8s:
    talosctl upgrade-k8s --to "$(just tuppr-version kubernetes)"

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

# ponytail: one address per node file, so a grep is enough; render and parse if that stops holding.
[private]
node-ips:
    grep -hoP '^\s+- address:\s*\K[0-9.]+' "{{ source_directory() }}"/nodes/*/*.yaml.j2 | sort -u | tr '\n' ' '

# Per-node override wins when present, otherwise the shared fleet schematic applies.
[private]
schematic-file node:
    for f in "{{ source_directory() }}"/nodes/*/"{{ node }}".schematic.yaml; do
        [[ -f "$f" ]] && { echo "$f"; exit 0; }
    done
    echo "{{ source_directory() }}/schematic.yaml"

# Schematics are plain YAML, not templates: POST the file directly rather than routing it through
# `template`, which would decrypt the secrets bundle to render a file that references no secrets.
[private]
schematic-id node:
    curl -sfX POST --data-binary @"$(just talos schematic-file "{{ node }}")" \
        https://factory.talos.dev/schematics | jq -r '.id'

[private]
machine-image node:
    just talos render-config "{{ node }}" | yq -r 'select(.machine.install != null) | .machine.install.image'
