# Cluster

Welcome to my fluxcd kubernetes cluster running on talos. This is based on the [cluster-template](https://github.com/onedr0p/cluster-template) project where I want to express my gratitude to the community for all the amazing work they have done.

## 📊 Cluster Statistics

[![Talos](https://kromgo.tanguille.site/talos_version?format=badge&style=flat-square&logo=kubernetes&logoColor=white&color=orange&label=talos)](https://www.talos.dev/)&nbsp;
[![Kubernetes](https://kromgo.tanguille.site/kubernetes_version?format=badge&style=flat-square&logo=kubernetes&logoColor=white&label=k8s)](https://www.talos.dev/)&nbsp;
[![Nodes](https://kromgo.tanguille.site/cluster_node_count?format=badge&style=flat-square)](https://github.com/kashalls/kromgo/)&nbsp;
[![Pods](https://kromgo.tanguille.site/cluster_pod_count?format=badge&style=flat-square)](https://github.com/kashalls/kromgo/)&nbsp;
[![CPU](https://kromgo.tanguille.site/cluster_cpu_usage?format=badge&style=flat-square)](https://github.com/kashalls/kromgo/)&nbsp;
[![Memory](https://kromgo.tanguille.site/cluster_memory_usage?format=badge&style=flat-square)](https://github.com/kashalls/kromgo/)&nbsp;
[![Age](https://kromgo.tanguille.site/cluster_age_days?format=badge&style=flat-square)](https://github.com/kashalls/kromgo/)&nbsp;
[![Uptime](https://kromgo.tanguille.site/cluster_uptime_days?format=badge&style=flat-square)](https://github.com/kashalls/kromgo/)&nbsp;

## 💥 Reset

There might be a situation where you want to destroy your Kubernetes cluster. The following command will reset your nodes back to maintenance mode, append `--force` to completely format your the Talos installation. Either way the nodes should reboot after the command has run.

```sh
task talos:reset # --force
```

## 🛠️ Talos and Kubernetes Maintenance

#### ⚙️ Updating Talos node configuration

📍 _Ensure you have updated `talconfig.yaml` and any patches with your updated configuration._

```sh
# (Re)generate the Talos config
task talos:generate-config
# Apply the config to the node
task talos:apply-node HOSTNAME=? MODE=?
# e.g. task talos:apply-config HOSTNAME=k8s-0 MODE=auto
```

#### ⬆️ Updating Talos and Kubernetes versions

📍 _Ensure the `talosVersion` and `kubernetesVersion` in `talhelper.yaml` are up-to-date with the version you wish to upgrade to._

```sh
# Upgrade node to a newer Talos version
task talos:upgrade-node HOSTNAME=?
# e.g. task talos:upgrade HOSTNAME=k8s-0
```

```sh
# Upgrade cluster to a newer Kubernetes version
task talos:upgrade-k8s
# e.g. task talos:upgrade-k8s
```

## 🐛 Debugging

Below is a general guide on trying to debug an issue with an resource or application. For example, if a workload/resource is not showing up or a pod has started but in a `CrashLoopBackOff` or `Pending` state.

1. Start by checking all Flux Kustomizations & Git Repository & OCI Repository and verify they are healthy.

   ```sh
   flux get sources oci -A
   flux get sources git -A
   flux get ks -A
   ```

2. Then check all the Flux Helm Releases and verify they are healthy.

   ```sh
   flux get hr -A
   ```

3. Then check the if the pod is present.

   ```sh
   kubectl -n <namespace> get pods -o wide
   ```

4. Then check the logs of the pod if its there.

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

Resolving problems that you have could take some tweaking of your YAML manifests in order to get things working, other times it could be a external factor like permissions on NFS. If you are unable to figure out your problem see the help section below.

### Ship it

To browse or get ideas on applications people are running, community member [@whazor](https://github.com/whazor) created [Kubesearch](https://kubesearch.dev) as a creative way to search Flux HelmReleases across Github and Gitlab.
