---
# Replaces machine.network.nameservers and machine.features.hostDNS, both of which now conflict
# with the ResolverConfig document topf's base emits. forwardKubeDNSToHost defaults to TRUE in
# that base, so the false below is load-bearing, not decoration.
apiVersion: v1alpha1
kind: ResolverConfig
nameservers:
  - address: {{ .Data.gateway }} # OPNsense Unbound
hostDNS:
  enabled: true
  forwardKubeDNSToHost: false
  resolveMemberNames: true
