set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

# Cluster-wide addresses, defined once: the VIP is referenced from every layer and from
# talosconfig, and the retired talconfig.yaml used a YAML anchor for the same reason.
vip := "192.168.0.2"
gateway := "192.168.0.1"

[doc('Render a node full machine config to stdout')]
render-config node:
    # Bare assignments: a failing command substitution inside an argument or array element does
    # NOT trip `set -e`, so these would silently render as empty.
    schematic="$(just talos schematic-id "{{ node }}")"
    role="$(just talos node-role "{{ node }}")"
    # Assignment alone catches a non-zero exit; these catch the other half, a producer that
    # succeeds with empty output. schematic feeds an install image path, where empty renders a
    # plausible ref that resolves to nothing.
    [[ -n "${schematic}" ]] || { echo "empty schematic id for {{ node }}" >&2; exit 1; }
    [[ -n "${role}" ]] || { echo "empty role for {{ node }}" >&2; exit 1; }
    # One context for every layer, so any layer may reference any of these.
    ctx=(-D "vip={{ vip }}" -D "gateway={{ gateway }}" -D "schematic=${schematic}")
    # Each layer is rendered to a variable BEFORE talosctl runs, not inline as <(just template).
    # A process substitution is asynchronous: its exit status never reaches talosctl, so a failed
    # layer was silently dropped. Verified by breaking controlplane.yaml.j2 -- render-config
    # exited 0 and emitted a config with no machine.type, which apply-node pipes straight to a
    # control-plane node. Assigning first also means a failure aborts before anything reaches
    # stdout, so a consumer can never see a partial config.
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
    # Assigned, not inlined: in argument position a failed substitution does not trip `set -e`,
    # so an empty image would reach `upgrade -i ""` and powercycle the node anyway.
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

# ponytail: one address per node file, so a grep is enough; render and parse if that stops holding.
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

# Schematics are plain YAML, not templates: POST the file directly rather than routing it through
# `template`, which would decrypt the secrets bundle to render a file that references no secrets.
# Bounded because a stalled factory blocks every render. Retries are safe: the endpoint is
# content-addressed, so re-POSTing identical content returns the same id.
# jq -er, not -r: a response without a usable .id must fail loudly, not print "null" and end up
# in an installer reference.
[private]
schematic-id node:
    # Assigned, not inlined into --data-binary: a command substitution in argument position
    # discards its exit status, so a failed lookup would POST the literal path "@" instead.
    file="$(just talos schematic-file "{{ node }}")"
    [[ -f "${file}" ]] || { echo "no schematic file for {{ node }}: ${file}" >&2; exit 1; }
    curl -sfX POST --connect-timeout 5 --max-time 30 --retry 3 --retry-max-time 60 \
        --data-binary @"${file}" \
        https://factory.talos.dev/schematics \
        | jq -er '.id | strings | select(length > 0)'

[private]
machine-image node:
    # -e, and checked: plain `yq -r` exits 0 with EMPTY output when the selector matches nothing,
    # and this value becomes `talosctl upgrade -i ""` against a live node. The selector breaks the
    # moment the install image moves document (UnattendedInstallConfig), which is not hypothetical.
    image="$(just talos render-config "{{ node }}" | yq -er 'select(.machine.install != null) | .machine.install.image')"
    [[ -n "${image}" ]] || { echo "no install image in rendered config for {{ node }}" >&2; exit 1; }
    echo "${image}"
