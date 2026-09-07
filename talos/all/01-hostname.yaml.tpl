---
# topf's `host` is only its own label for the node; Talos still auto-generates talos-XXX-XXX
# without this document.
apiVersion: v1alpha1
kind: HostnameConfig
auto: "off"
hostname: {{ .Node.Host }}
