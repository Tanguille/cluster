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
    yq -e '.spec.{{ kind }}.version' \
        "{{ justfile_directory() }}/kubernetes/apps/system-upgrade/tuppr/upgrades/{{ kind }}upgrade.yaml"

# Decrypts on stdout only; callers pipe it so the plaintext lives no longer than the command
# and never reaches disk or a shell variable.
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
    talos_version="$(just tuppr-version talos)"
    kubernetes_version="$(just tuppr-version kubernetes)"
    # Renovate maintains the kernel version in one place, the Dockerfile ARG. Reading it here
    # stops a node advertising a version whose image was never built. Guarded because sed exits
    # 0 with empty output, which --strict cannot catch.
    kernel_version="$(sed -nE 's/^ARG KERNEL_VERSION=(.+)$/\1/p' "{{ justfile_directory() }}/docker/talos-kernel/Dockerfile")"
    [ -n "${kernel_version}" ] || { echo "no 'ARG KERNEL_VERSION=' in docker/talos-kernel/Dockerfile" >&2; exit 1; }
    # Piped, not <(just talsecret): through a pipe `pipefail` sees a failed decrypt.
    just talsecret | minijinja-cli --strict --format=yaml --autoescape=none \
        -D "talosVersion=${talos_version}" \
        -D "kubernetesVersion=${kubernetes_version}" \
        -D "kernelVersion=${kernel_version}" \
        -D "pinned=${talos_version}-k${kernel_version}" \
        {{ args }} "{{ file }}" -

[doc('Force Flux to pull in changes from the Git repository')]
reconcile:
    flux reconcile source git flux-system
    flux reconcile kustomization cluster-apps -n flux-system --with-source
    flux reconcile kustomization flux-system --with-source
