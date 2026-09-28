from pathlib import Path
import unittest

import yaml


class ClusterManifestTests(unittest.TestCase):
    def test_addon_tasks_use_only_pinned_release_urls(self):
        text = Path("ansible/roles/cluster_addons/tasks/main.yml").read_text()
        self.assertIn("projectcalico/calico/v{{ calico_version }}", text)
        self.assertIn("gateway-api/releases/download/v{{ gateway_api_version }}", text)
        self.assertIn("--version {{ traefik_chart_version }}", text)
        self.assertIn("local-path-provisioner/v{{ local_path_provisioner_version }}", text)
        self.assertNotIn("/latest/", text)
        self.assertNotIn("/master/", text)

    def test_traefik_is_private_nodeport_gateway(self):
        values = Path(
            "ansible/roles/cluster_addons/templates/traefik-values.yml.j2"
        ).read_text()
        self.assertIn("type: NodePort", values)
        self.assertIn("nodePort: {{ traefik_http_node_port }}", values)
        self.assertIn("kubernetesGateway", values)
        self.assertNotIn("LoadBalancer", values)

    def test_traefik_binds_public_host_ports_on_the_worker(self):
        values = yaml.safe_load(
            Path("ansible/roles/cluster_addons/templates/traefik-values.yml.j2")
            .read_text()
            .replace("{{ traefik_http_node_port }}", "30080")
        )
        self.assertEqual(values["ports"]["web"]["hostPort"], 80)
        self.assertEqual(values["ports"]["websecure"]["hostPort"], 443)
        self.assertEqual(values["nodeSelector"], {"node-role.kubernetes.io/worker": ""})
        self.assertEqual(
            values["updateStrategy"]["rollingUpdate"], {"maxUnavailable": 1, "maxSurge": 0}
        )

    def test_calico_rollout_waits_for_operator_created_daemonset(self):
        tasks = yaml.safe_load(
            Path("ansible/roles/cluster_addons/tasks/main.yml").read_text()
        )
        names = [t["name"] for t in tasks]
        create_wait = tasks[
            names.index("Wait for the operator to create the Calico node DaemonSet")
        ]
        self.assertIn("get daemonset/calico-node", create_wait["ansible.builtin.command"])
        self.assertIn("until", create_wait)
        self.assertLess(
            names.index("Wait for the operator to create the Calico node DaemonSet"),
            names.index("Wait for Calico on every node"),
        )

    def test_worker_label_targets_the_kubernetes_node_name(self):
        tasks = yaml.safe_load(
            Path("ansible/roles/cluster_addons/tasks/main.yml").read_text()
        )
        label = next(t for t in tasks if t["name"].startswith("Label the worker node"))
        self.assertIn(
            "label node {{ hostvars[groups['kube_workers'] | first].gcp_instance_name }}",
            label["ansible.builtin.command"],
        )

    def test_local_path_rejects_unlisted_nodes(self):
        config = Path(
            "ansible/roles/cluster_addons/templates/local-path-config.yml.j2"
        ).read_text()
        # Kubernetes registers nodes by GCP instance name, not inventory name.
        self.assertIn(
            '"node": "{{ hostvars[groups[\'kube_workers\'] | first].gcp_instance_name }}"',
            config,
        )
        self.assertIn('"node": "DEFAULT_PATH_FOR_NON_LISTED_NODES"', config)
        self.assertIn('"paths": []', config)
        self.assertIn("{{ local_path_root }}", config)

    VALIDATION_PLAYBOOKS = (
        "ansible/playbooks/validate_cluster.yml",
        "ansible/playbooks/validate_services.yml",
    )

    @staticmethod
    def _tasks(path):
        return yaml.safe_load(Path(path).read_text())[0]["tasks"]

    def test_validation_playbooks_contain_only_read_only_checks(self):
        for path in self.VALIDATION_PLAYBOOKS:
            with self.subTest(playbook=path):
                tasks = self._tasks(path)
                commands = [
                    task["ansible.builtin.command"]
                    for task in tasks
                    if "ansible.builtin.command" in task
                ]
                for command in commands:
                    self.assertRegex(command, r"^kubectl .*\b(get|wait|rollout status)\b")
                    self.assertNotRegex(command, r"\b(apply|delete|patch|label|annotate|scale)\b")
                requests = [task["ansible.builtin.uri"] for task in tasks if "ansible.builtin.uri" in task]
                self.assertEqual(len(commands) + len(requests), len(tasks))
                self.assertTrue(all(request["method"] == "GET" for request in requests))
                self.assertTrue(all(task["changed_when"] is False for task in tasks))

    def test_validation_waits_for_workloads_after_worker_restart(self):
        tasks = self._tasks("ansible/playbooks/validate_cluster.yml")
        names = [task["name"] for task in tasks]
        wait = tasks[names.index("Wait for both Kubernetes nodes to be Ready")]
        self.assertIn("wait --for=condition=Ready nodes --all", wait["ansible.builtin.command"])
        rollouts = tasks[names.index("Wait for cluster add-ons after restart")]
        self.assertIn("rollout status", rollouts["ansible.builtin.command"])
        self.assertEqual(
            {(item["namespace"], item["resource"]) for item in rollouts["loop"]},
            {
                ("calico-system", "daemonset/calico-node"),
                ("kube-system", "deployment/coredns"),
                ("local-path-storage", "deployment/local-path-provisioner"),
                ("traefik", "deployment/traefik"),
            },
        )
        self.assertLess(names.index("Wait for cluster add-ons after restart"), names.index("List system pods"))


if __name__ == "__main__":
    unittest.main()
