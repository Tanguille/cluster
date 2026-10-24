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

    def test_runtime_and_pending_migration_gate(self):
        hr = load(APP / "app/helmrelease.yaml")["spec"]
        self.assertTrue(hr["suspend"], "Remove this gate only after approved migration/adoption")
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

    def test_backup_removal_and_pvc_retention(self):
        self.assertFalse((APP / "app/backup.yaml").exists())
        self.assertNotIn("backup.yaml", load(APP / "app/kustomization.yaml")["resources"])
        self.assertEqual([{"name": "cloudnative-pg-databases", "namespace": "database"}],
                         load(APP / "ks.yaml")["spec"]["dependsOn"])
        pvcs = list(yaml.safe_load_all((APP / "app/pvc.yaml").read_text()))
        self.assertEqual({"bitmagnet-pgdata", "armiger-connector-data"},
                         {p["metadata"]["name"] for p in pvcs})
        for pvc in pvcs:
            self.assertEqual("disabled", pvc["metadata"]["annotations"]
                             ["kustomize.toolkit.fluxcd.io/prune"])
            self.assertEqual("5Gi", pvc["spec"]["resources"]["requests"]["storage"])


if __name__ == "__main__":
    unittest.main()
