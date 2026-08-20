set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

[group('Kube')]
mod kube "kubernetes"

[group('Talos')]
mod talos "talos"

# Renders a template with the decrypted Talos secrets as its context, so templates
# reference talsecret.sops.yaml's own key names ({{ certs.os.crt }}) with no plaintext
# ever hitting disk. Talos/Kubernetes versions come from the tuppr CRs so they stay
# single-sourced and Renovate-managed. --strict makes a typo'd variable an error, not "".
[private]
template file *args:
    upgrades="{{ justfile_directory() }}/kubernetes/apps/system-upgrade/tuppr/upgrades"
    minijinja-cli --strict --format=yaml --autoescape=none \
        -D "talosVersion=$(yq '.spec.talos.version' "${upgrades}/talosupgrade.yaml")" \
        -D "kubernetesVersion=$(yq '.spec.kubernetes.version' "${upgrades}/kubernetesupgrade.yaml")" \
        {{ args }} "{{ file }}" <(sops -d "{{ justfile_directory() }}/talos/talsecret.sops.yaml")

[doc('Force Flux to pull in changes from the Git repository')]
reconcile:
    flux reconcile source git flux-system
    flux reconcile kustomization cluster-apps -n flux-system --with-source
    flux reconcile kustomization flux-system --with-source
