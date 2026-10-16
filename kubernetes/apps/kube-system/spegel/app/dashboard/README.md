# Spegel dashboard

Based on [Spegel v0.7.4's last bundled dashboard](https://github.com/spegel-org/spegel/blob/v0.7.4/charts/spegel/monitoring/grafana-dashboard.json), licensed under MIT (see LICENSE).

Adapted for Spegel 0.8.0: advertised keys replace the removed image/content digest breakdown; peer resolve latency occupies the former layer-count panel. Keys are counted per peer, not as unique cluster images. Pod discovery uses scrape targets; registry discovery uses mirror requests. Cache hit/miss totals cover the selected time range. HTTP latency uses a histogram percentile, and CPU is scoped to selected Spegel pods.

Kustomize generates the dashboard ConfigMap with Flux substitution disabled so Grafana variables survive. Grafana Operator imports it; the datasource defaults to `prometheus` and remains selectable. Preserve the upstream dashboard uid and panel ids when updating.
