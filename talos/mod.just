set quiet
set default-list
set default-script
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

upgrades := justfile_directory() / 'kubernetes/apps/system-upgrade/tuppr/upgrades'

# talconfig.yaml substitutes ${talosVersion} and ${kubernetesVersion} at parse time, so
# every talhelper call needs both in the environment. Evaluated only when a recipe runs.
export talosVersion := shell('yq $1 $2', '.spec.talos.version', upgrades / 'talosupgrade.yaml')
export kubernetesVersion := shell('yq $1 $2', '.spec.kubernetes.version', upgrades / 'kubernetesupgrade.yaml')

[doc('Generate Talos configuration')]
[working-directory(source_directory())]
generate-config:
    talhelper genconfig

[doc('Apply Talos config to a node')]
[working-directory(source_directory())]
apply-node node mode='auto':
    talhelper gencommand apply --node {{ node }} --extra-flags "--mode={{ mode }}" | bash

[doc('Upgrade Talos on a single node')]
[working-directory(source_directory())]
upgrade-node node:
    image="$(yq '.nodes[] | select(.ipAddress == "{{ node }}") | .talosImageURL' talconfig.yaml)"
    talhelper gencommand upgrade --node {{ node }} \
        --extra-flags "--image='${image}:${talosVersion}' --timeout=15m" | bash

[doc('Upgrade Kubernetes')]
[working-directory(source_directory())]
upgrade-k8s:
    talhelper gencommand upgrade-k8s --extra-flags "--to ${kubernetesVersion}" | bash

# wipe=false skips the STATE/EPHEMERAL wipe, leaving the installed system on disk.
[confirm('This will destroy the cluster and reset nodes to maintenance mode. Continue? [y|N]')]
[doc('Reset nodes back to maintenance mode')]
[working-directory(source_directory())]
reset wipe='true':
    talhelper gencommand reset --extra-flags="--reboot --graceful=false --wait=false{{ if wipe == 'true' { ' --system-labels-to-wipe STATE --system-labels-to-wipe EPHEMERAL' } else { '' } }}" | bash

[doc('Build both schematics and update talconfig.yaml with new installer URLs')]
schematics-update:
    TALOS_DIR="{{ source_directory() }}" "{{ justfile_directory() }}/scripts/talos-schematics-update.sh"
