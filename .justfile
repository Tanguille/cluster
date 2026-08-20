set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

[group('Kube')]
mod kube "kubernetes"

[group('Talos')]
mod talos "talos"

# The tuppr upgrade CRs are the single source for both versions, so Renovate manages them in
# one place. `kind` names both the file and the spec key (talos -> talosupgrade.yaml).
[private]
tuppr-version kind:
    yq '.spec.{{ kind }}.version' \
        "{{ justfile_directory() }}/kubernetes/apps/system-upgrade/tuppr/upgrades/{{ kind }}upgrade.yaml"

# Decrypts on stdout only; callers consume it through process substitution so the plaintext
# lives no longer than the command and never reaches disk or a shell variable.
[private]
talsecret:
    sops -d "{{ justfile_directory() }}/talos/talsecret.sops.yaml"

# Renders a template with the decrypted Talos secrets as its context, so templates reference
# talsecret.sops.yaml's own key names ({{ certs.os.crt }}).
# --autoescape=none is required: the default JSON-escapes every substitution, which silently
# wraps certs and versions in quotes and yields a config that looks right and is not.
# --strict makes a typo'd variable an error rather than an empty string.
[private]
template file *args:
    minijinja-cli --strict --format=yaml --autoescape=none \
        -D "talosVersion=$(just tuppr-version talos)" \
        -D "kubernetesVersion=$(just tuppr-version kubernetes)" \
        {{ args }} "{{ file }}" <(just talsecret)

[doc('Force Flux to pull in changes from the Git repository')]
reconcile:
    flux reconcile source git flux-system
    flux reconcile kustomization cluster-apps -n flux-system --with-source
    flux reconcile kustomization flux-system --with-source
