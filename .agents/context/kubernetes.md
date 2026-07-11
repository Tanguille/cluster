# Kubernetes and Flux Context

**When to use:** HTTPRoute, Flux, Reloader, in-cluster URL, privileged pod, zap, substitution, deployment strategy, or app-template.

- Use in-cluster service URLs such as `http://service.namespace.svc.cluster.local:port` for pod-to-pod calls to avoid external DNS and hairpinning.
- Annotate deployments with `reloader.stakater.com/auto: "true"` so pods restart when referenced ConfigMaps change.
- Follow KISS principles; avoid init containers or extra complexity unless required.
- Internal HTTPRoutes use parentRef name `envoy-internal`; k8s-gateway serves DNS for routes attached to that gateway.
- For one-shot privileged pods such as disk zap jobs, use a YAML manifest and `kubectl apply -f`; complex `kubectl run --overrides` JSON is unreliable.
- Before re-running the same one-shot pod, delete it with `kubectl delete pod <name> --ignore-not-found`; pod specs are largely immutable.
- Flux `postBuild.substituteFrom` replaces `${...}` in rendered manifests. Escape literals the shell must see with Flux/Kustomize `$$` patterns, or Flux may empty unintended matches.
- The app-template chart defaults to deployment strategy `Recreate`. If a chart emits `rollingUpdate` alongside `Recreate`, Kubernetes rejects it; fix the chart, postRenderer, or patch.
