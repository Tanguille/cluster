set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']

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
[private]
template file *args:
    # autoescape/strict come from .minijinja.toml via this var. Unset, autoescaping returns and
    # renders `crt: "LS0t..."` -- right-looking, wrong value. Fail loudly instead.
    [[ -n "${MINIJINJA_CONFIG_FILE:-}" ]] || {
        echo "MINIJINJA_CONFIG_FILE unset (mise sets it; try a new shell or 'mise env')" >&2
        exit 1
    }
    talos_version="$(just tuppr-version talos)"
    kubernetes_version="$(just tuppr-version kubernetes)"
    # Piped, not <(just talsecret): through a pipe `pipefail` sees a failed decrypt.
    just talsecret | minijinja-cli --format=yaml \
        -D "kubernetesVersion=${kubernetes_version}" \
        -D "talosVersion=${talos_version}" \
        {{ args }} "{{ file }}" -

[doc('Force Flux to pull in changes from the Git repository')]
reconcile:
    # --with-source fetches the GitRepository itself, so only the first call needs it.
    flux reconcile kustomization flux-system --with-source
    flux reconcile kustomization cluster-apps
