# Cluster

Welcome to my fluxcd kubernetes cluster running on talos. This is based on the [cluster-template](https://github.com/onedr0p/cluster-template) project where I want to express my gratitude to the community for all the amazing work they have done.

## 💥 Reset

There might be a situation where you want to destroy your Kubernetes cluster. The following command will reset your nodes back to maintenance mode, append `--force` to completely format your the Talos installation. Either way the nodes should reboot after the command has sucessfully ran.

```sh
task talos:reset # --force
```

## 🛠️ Talos and Kubernetes Maintenance

### ⚙️ Updating Talos node configuration

> [!IMPORTANT]
> Ensure you have updated `talconfig.yaml` and any patches with your updated configuration. In some cases you **not only need to apply the configuration but also upgrade talos** to apply new configuration.

```sh
# (Re)generate the Talos config
task talos:generate-config
# Apply the config to the node
task talos:apply-node IP=? MODE=?
# e.g. task talos:apply-node IP=10.10.10.10 MODE=auto
```

### ⬆️ Updating Talos and Kubernetes versions

> [!IMPORTANT]
> Ensure the `talosVersion` and `kubernetesVersion` in `talconfig.yaml` are up-to-date with the version you wish to upgrade to.

```sh
# Upgrade node to a newer Talos version
task talos:upgrade-node IP=?
# e.g. task talos:upgrade-node IP=10.10.10.10
```

```sh
# Upgrade cluster to a newer Kubernetes version
task talos:upgrade-k8s
# e.g. task talos:upgrade-k8s
```

## 🐛 Debugging

Below is a general guide on trying to debug an issue with an resource or application. For example, if a workload/resource is not showing up or a pod has started but in a `CrashLoopBackOff` or `Pending` state. Most of these steps do not include a way to fix the problem as the problem could be one of many different things.

1. Verify the Git Repository is up-to-date and in a ready state.

   ```sh
   flux get sources oci -A
   flux get sources git -A
   flux get ks -A
   ```

2. Verify all the Flux helm releases are up-to-date and in a ready state.

   ```sh
   flux get hr -A
   ```

3. Do you see the pod of the workload you are debugging?

   ```sh
   kubectl -n <namespace> get pods -o wide
   ```

4. Check the logs of the pod if its there.

   ```sh
   kubectl -n <namespace> logs <pod-name> -f
   # or
   stern -n <namespace> <fuzzy-name>
   ```

5. If a resource exists try to describe it to see what problems it might have.

   ```sh
   kubectl -n <namespace> describe <resource> <name>
   ```

6. Check the namespace events

   ```sh
   kubectl -n <namespace> get events --sort-by='.metadata.creationTimestamp'
   ```

Resolving problems that you have could take some tweaking of your YAML manifests in order to get things working, other times it could be a external factor like permissions on a NFS server.

### Community Repositories

Community member [@whazor](https://github.com/whazor) created [Kubesearch](https://kubesearch.dev) to allow searching Flux HelmReleases across Github and Gitlab repositories with the `kubesearch` topic.

## 🤝 Thanks

Big shout out to all the contributors, sponsors and everyone else who has helped on this project, especially the upstream [cluster-template](https://github.com/onedr0p/cluster-template) project.
