"""Offline shared-CNPG wiring safeguards; requires PyYAML."""
from pathlib import Path
import unittest
import yaml

APP = Path(__file__).resolve().parents[1]
CNPG = APP.parents[1] / "database/cloudnative-pg"


def load(path):
    return yaml.safe_load(path.read_text())


class DatabaseWiringTests(unittest.TestCase):
    def test_shared_database_and_restricted_role(self):
        spec = load(CNPG / "databases/resourceset.yaml")["spec"]
        self.assertIn({"name": "bitmagnet"}, spec["inputs"])
        database = spec["resources"][0]["spec"]
        self.assertEqual("postgres16", database["cluster"]["name"])
        self.assertEqual("<< inputs.name >>", database["owner"])
        self.assertEqual("retain", database["databaseReclaimPolicy"])
        role = next(r for r in load(CNPG / "cluster/cluster.yaml")["spec"]["managed"]["roles"]
                    if r["name"] == "bitmagnet")
        self.assertTrue(role["login"])
        for privilege in ("superuser", "createdb", "createrole"):
            self.assertFalse(role[privilege])
        self.assertEqual({"name": "bitmagnet-db"}, role["passwordSecret"])
        self.assertIn("roles/bitmagnet.sops.yaml", load(CNPG / "cluster/kustomization.yaml")["resources"])
        secret = load(CNPG / "cluster/roles/bitmagnet.sops.yaml")
        self.assertEqual("bitmagnet-db", secret["metadata"]["name"])
        self.assertEqual("database", secret["metadata"]["namespace"])
        self.assertEqual("kubernetes.io/basic-auth", secret["type"])
        for key in ("username", "password"):
            self.assertTrue(secret["stringData"][key].startswith("ENC["))

    def test_runtime_ready_for_fresh_install(self):
        hr = load(APP / "app/helmrelease.yaml")["spec"]
        self.assertFalse(hr.get("suspend", False))
        self.assertIn("./bitmagnet/ks.yaml", load(APP.parent / "kustomization.yaml")["resources"])
        values = hr["values"]
        self.assertEqual({"bitmagnet", "connector"}, set(values["controllers"]))
        self.assertNotIn("postgres", values["service"])
        env = values["controllers"]["bitmagnet"]["containers"]["app"]["env"]
        self.assertEqual("bitmagnet", env["POSTGRES_USER"])
        self.assertEqual("bitmagnet", env["POSTGRES_NAME"])
        self.assertEqual("postgres16-rw.database.svc.cluster.local", env["POSTGRES_HOST"])
        self.assertEqual({"name": "bitmagnet-postgres", "key": "POSTGRES_PASSWORD"},
                         env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"])
        self.assertNotIn("bitmagnet-pgdata", [v.get("existingClaim") for v in values["persistence"].values()])
        connector = values["controllers"]["connector"]
        self.assertIn("bootstrap", connector["initContainers"])
        self.assertEqual("both", connector["containers"]["app"]["env"]["ARMIGER_CONNECTOR_MODE"])

    def test_chart_manages_retained_connector_storage_and_dht_policy(self):
        self.assertFalse((APP / "app/backup.yaml").exists())
        self.assertNotIn("backup.yaml", load(APP / "app/kustomization.yaml")["resources"])
        self.assertEqual([{"name": "cloudnative-pg-databases", "namespace": "database"}],
                         load(APP / "ks.yaml")["spec"]["dependsOn"])
        app = load(APP / "app/helmrelease.yaml")["spec"]["values"]
        pvc = app["persistence"]["connector-data"]
        self.assertEqual("persistentVolumeClaim", pvc["type"])
        self.assertEqual("armiger-connector-data", pvc["forceRename"])
        self.assertEqual("ReadWriteOnce", pvc["accessMode"])
        self.assertEqual("5Gi", pvc["size"])
        self.assertEqual("ceph-block", pvc["storageClass"])
        self.assertTrue(pvc["retain"])
        self.assertEqual([{"path": "/data/armiger"}],
                         pvc["advancedMounts"]["connector"]["bootstrap"])
        self.assertEqual([{"path": "/data/armiger"}],
                         pvc["advancedMounts"]["connector"]["app"])
        policy = app["networkpolicies"]["dht"]
        self.assertEqual("cilium", policy["type"])
        self.assertEqual("bitmagnet-dht", policy["forceRename"])
        self.assertEqual({"matchLabels": {
            "app.kubernetes.io/instance": "bitmagnet",
            "app.kubernetes.io/controller": "bitmagnet",
        }}, policy["endpointSelector"])
        self.assertEqual([{"fromEntities": ["world"], "toPorts": [{"ports": [
            {"port": "3334", "protocol": "TCP"},
            {"port": "3334", "protocol": "UDP"},
        ]}]}], policy["ingress"])
        resources = load(APP / "app/kustomization.yaml")["resources"]
        self.assertNotIn("pvc.yaml", resources)
        self.assertNotIn("networkpolicy.yaml", resources)
        self.assertFalse((APP / "app/pvc.yaml").exists())
        self.assertFalse((APP / "app/networkpolicy.yaml").exists())


if __name__ == "__main__":
    unittest.main()
