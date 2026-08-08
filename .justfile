set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

[group('Kube')]
mod kube "kubernetes"

[group('Talos')]
mod talos "talos"

[doc('Force Flux to pull in changes from the Git repository')]
reconcile:
    flux reconcile source git flux-system
    flux reconcile kustomization cluster-apps -n flux-system --with-source
    flux reconcile kustomization flux-system --with-source
