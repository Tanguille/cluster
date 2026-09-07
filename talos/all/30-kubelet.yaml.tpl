---
# KubeletConfig is deleted and the deprecated v1alpha1 machine.kubelet kept in its place, to
# hold the /var/openebs/local mount that openebs-localpv needs. KubeletConfig's ExtraMounts()
# is `return nil` (machinery v1.14.0 k8s/kubelet.go:189-192) and the two are mutually
# exclusive, so $patch: delete is the only way to keep it.
# See "The kubelet exception" in README.md for the two ways out of this.
apiVersion: v1alpha1
kind: KubeletConfig
$patch: delete
---
machine:
  kubelet:
    image: ghcr.io/siderolabs/kubelet:{{ .KubernetesVersion }}
    defaultRuntimeSeccompProfileEnabled: true
    disableManifestsDirectory: true
    extraConfig:
      crashLoopBackOff:
        maxContainerRestartPeriod: 60s
      enableSystemLogHandler: true
      enableSystemLogQuery: true
      mergeDefaultEvictionSettings: true
      evictionHard:
        # Flat, not a percentage: 7% cost 2.04Gi on the 29GB nodes and 4.39Gi on the 64GB one,
        # so the big node set aside twice the reserve the small ones got. Still 10x the k8s
        # default of 100Mi, against 8.7-10GB of reclaimable page cache per node.
        memory.available: 1Gi
      evictionMinimumReclaim:
        memory.available: 1Gi
      # Trigger image GC before Ceph's mon_data_avail_warn default (30% free) fires.
      imageGCHighThresholdPercent: 70
      imageGCLowThresholdPercent: 55
      # Reap images unused for 7d regardless of disk pressure.
      imageMaximumGCAge: 168h
      # Left at 2Gi despite being under-reserved: measured /podruntime is 2.89GB steady-state on
      # control-2 and peaked at 14.1GB on control-3. Raising it to the truth costs allocatable on
      # nodes that have none to spare, so the under-reservation is accepted deliberately.
      kubeReserved:
        cpu: 500m
        memory: 2Gi
      maxPods: 200
      serializeImagePulls: false
      systemReserved:
        cpu: 500m
        # Measured /system + /init peak is 391MB on control-3, 203MB on control-2.
        memory: 512Mi
    extraMounts:
      - destination: /var/openebs/local
        type: bind
        source: /var/openebs/local
        options:
          - bind
          - rshared
          - rw
---
# nodeIP moved off machine.kubelet: KubeNodeConfig owns it in 1.14 and the two conflict.
apiVersion: v1alpha1
kind: KubeNodeConfig
nodeIP:
  validSubnets:
    - {{ .Data.nodeCidr }}
