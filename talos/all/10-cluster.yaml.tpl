---
# certSANs has no typed document; topf's generated base leaves it as an empty v1alpha1 list.
machine:
  certSANs:
    - 127.0.0.1
    - {{ .Data.vip }}
---
# Replaces cluster.network. Overriding is mandatory, not cosmetic: topf's base defaults to
# 10.244.0.0/16 and 10.96.0.0/12.
apiVersion: v1alpha1
kind: KubeNetworkConfig
dnsDomain: cluster.local
podSubnets: ["10.42.0.0/16"]
serviceSubnets: ["10.43.0.0/16"]
