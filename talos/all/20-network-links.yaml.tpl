---
# Addresses attach to the MAC-matched link directly. Upstream wraps it in a single-link
# active-backup bond0 so a second NIC can join later without renaming; not adopted here because
# these nodes have one NIC in use and renaming the interface would be a live-cluster change for
# no benefit.
apiVersion: v1alpha1
kind: LinkAliasConfig
name: ethSel0
selector:
  match: glob("{{ .Node.Data.macAddr }}", mac(link.hardware_addr))
---
apiVersion: v1alpha1
kind: LinkConfig
name: ethSel0
# 1500, not 9000: the switch passes jumbo frames but control-1's hypervisor VM NIC blackholes
# them.
mtu: 1500
addresses:
  - address: {{ .Node.IP }}/{{ (splitList "/" .Data.nodeCidr) | last }}
routes:
  - gateway: {{ .Data.gateway }}
---
apiVersion: v1alpha1
kind: Layer2VIPConfig
name: {{ .Data.vip }}
link: ethSel0
