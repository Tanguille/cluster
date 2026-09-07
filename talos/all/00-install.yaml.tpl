---
# Replaces machine.install, which Talos 1.14 rejects alongside this document.
#
# Image AND diskSelector must live in the SAME document: topf prepends its own
# UnattendedInstallConfig (factory image, hardcoded /dev/sda) and cel.Expression.Merge
# overwrites unconditionally, so an image-only override would silently blank the selector and
# render a config Talos rejects with "provisioning.diskSelector.match is required".
#
# The installer is ours, not the Factory's: tuppr rebuilds the upgrade target as
# "<repo>:<targetVersion>", so a factory.talos.dev ref here would reinstall the stock kernel.
# See docker/talos-kernel/README.md "Version tagging".
#
# grubUseUKICmdline is not set and is not lost: it is unconditionally true whenever
# UnattendedInstallConfig is used (talos cmd/installer/cmd/installer/install.go:96-97).
apiVersion: v1alpha1
kind: UnattendedInstallConfig
installer:
  image: ghcr.io/tanguille/installer/{{ .Node.Data.installer }}:{{ .Data.installerTag }}
provisioning:
  wipe: false
  diskSelector:
    # The symlinks clause is load-bearing for control-3 alone: a by-id path is never dev_path.
    match: disk.dev_path == "{{ .Node.Data.installDisk }}" || "{{ .Node.Data.installDisk }}" in disk.symlinks
