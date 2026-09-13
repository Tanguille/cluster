set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

[doc('Encrypt all Kubernetes SOPS secrets')]
encrypt:
    find "{{ source_directory() }}" -type f -name '*.sops.*' | while read -r file; do
        if sops filestatus "$file" | jq --exit-status '.encrypted == false' &>/dev/null; then
            sops --encrypt --in-place "$file"
        fi
    done

# One call, not one per kind: 3x faster, at the cost of silently omitting empty kinds.
[doc('Gather common resources in the cluster, useful when asking for support')]
resources *args='-A':
    kubectl get nodes,gitrepositories,kustomizations,helmrepositories,helmreleases,\
    certificates,certificaterequests,pods {{ args }}

# https://docs.github.com/en/enterprise-cloud@latest/actions/hosting-your-own-runners/managing-self-hosted-runners-with-actions-runner-controller/deploying-runner-scale-sets-with-actions-runner-controller#upgrading-arc
[doc('Upgrade the Actions Runner Controller')]
upgrade-arc:
    # Enumerated, not hardcoded: every scale set must be uninstalled before the controller.
    for r in $(helm -n actions-runner-system ls -q | grep -v '^actions-runner-controller$'); do
        helm -n actions-runner-system uninstall "$r"
    done
    helm -n actions-runner-system uninstall actions-runner-controller
    sleep 5
    flux -n actions-runner-system reconcile hr actions-runner-controller
    for r in $(flux -n actions-runner-system get hr --no-header | awk '{print $1}' | grep -v '^actions-runner-controller$'); do
        flux -n actions-runner-system reconcile hr "$r"
    done
