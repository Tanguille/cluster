---
apiVersion: v1alpha1
kind: TimeSyncConfig
ntp:
  servers:
    - {{ .Data.gateway }}
