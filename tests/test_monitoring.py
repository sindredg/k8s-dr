from pathlib import Path
import unittest

import yaml

MONITORING = Path("deploy/monitoring")
SYNC = Path("deploy/clusters/primary/sync.yaml")


def _documents(path):
    return [document for document in yaml.safe_load_all(path.read_text()) if document]


def _sync():
    return {document["metadata"]["name"]: document for document in _documents(SYNC)}


def _release():
    return _documents(MONITORING / "release.yaml")[0]


class MonitoringKustomizationTests(unittest.TestCase):
    def test_monitoring_is_independent_of_the_service(self):
        # A failed chart or an unreachable Grafana Cloud must not block Gitea,
        # a bootstrap, or a recovery. See decision 0009.
        spec = _sync()["monitoring"]["spec"]
        self.assertEqual(spec["path"], "./deploy/monitoring")
        self.assertFalse(spec["wait"])
        self.assertNotIn("dependsOn", spec)
        for name, kustomization in _sync().items():
            with self.subTest(name=name):
                self.assertNotIn(
                    {"name": "monitoring"}, kustomization["spec"].get("dependsOn", [])
                )

    def test_decrypts_the_token_and_reads_the_cluster_name(self):
        spec = _sync()["monitoring"]["spec"]
        self.assertEqual(
            spec["decryption"], {"provider": "sops", "secretRef": {"name": "sops-age"}}
        )
        self.assertEqual(
            spec["postBuild"],
            {"substituteFrom": [{"kind": "ConfigMap", "name": "cluster-settings"}]},
        )


class MonitoringReleaseTests(unittest.TestCase):
    def setUp(self):
        self.release = _release()
        self.values = self.release["spec"]["values"]

    def test_chart_is_pinned(self):
        chart = self.release["spec"]["chart"]["spec"]
        self.assertEqual((chart["chart"], chart["version"]), ("k8s-monitoring", "4.5.2"))
        repository = _documents(MONITORING / "repository.yaml")[0]
        self.assertEqual(repository["spec"]["url"], "https://grafana.github.io/helm-charts")

    def test_labels_metrics_with_the_cluster_name(self):
        self.assertEqual(self.values["cluster"], {"name": "${cluster_name}"})

    def test_collects_only_cluster_and_host_metrics(self):
        features = {
            key for key, value in self.values.items()
            if isinstance(value, dict) and value.get("enabled")
        }
        self.assertEqual(features, {"clusterMetrics", "hostMetrics"})
        self.assertFalse(self.values["selfReporting"]["enabled"])
        self.assertEqual(
            {name for name, service in self.values["telemetryServices"].items() if service["deploy"]},
            {"kube-state-metrics", "node-exporter"},
        )

    def test_configuration_comes_from_git_not_fleet_management(self):
        self.assertNotIn("remoteConfig", str(self.values))

    def test_destination_reads_the_encrypted_secret(self):
        destination = self.values["destinations"]["grafanaCloudMetrics"]
        self.assertEqual(destination["type"], "prometheus")
        self.assertEqual(destination["secret"], {"create": False, "name": "grafana-cloud-metrics"})
        self.assertNotIn("password", destination["auth"])
        secret = _documents(MONITORING / "grafana-cloud-metrics.sops.yaml")[0]
        self.assertEqual(secret["metadata"]["name"], "grafana-cloud-metrics")
        self.assertEqual(secret["metadata"]["namespace"], "monitoring")
        self.assertEqual(set(secret["stringData"]), {"username", "password"})

    def test_every_workload_has_memory_requests_and_limits(self):
        # The nodes have 4 GiB each. See decision 0009.
        resources = {
            "alloy-metrics": self.values["collectors"]["alloy-metrics"]["alloy"]["resources"],
            "alloy-operator": self.values["alloy-operator"]["resources"],
            "kube-state-metrics": self.values["telemetryServices"]["kube-state-metrics"]["resources"],
            "node-exporter": self.values["telemetryServices"]["node-exporter"]["resources"],
        }
        for name, resource in resources.items():
            with self.subTest(name=name):
                self.assertIn("memory", resource["requests"])
                self.assertIn("memory", resource["limits"])


if __name__ == "__main__":
    unittest.main()
