from pathlib import Path
import stat
import subprocess
import tempfile
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

    def test_disposable_app_uses_worker_pvc_and_http_route(self):
        text = Path("ansible/manifests/milestone2-app.yml").read_text()
        documents = [document for document in yaml.safe_load_all(text) if document]
        kinds = {document["kind"] for document in documents}
        self.assertTrue(
            {"PersistentVolumeClaim", "Deployment", "Service", "Gateway", "HTTPRoute"}
            <= kinds
        )
        self.assertIn("nginx:1.28.0-alpine3.21", text)
        self.assertIn("busybox:1.37.0", text)
        self.assertIn("node-role.kubernetes.io/worker", text)
        self.assertNotIn(":latest", text)

    def test_disposable_app_makes_existing_volume_readable_by_nginx(self):
        documents = list(
            yaml.safe_load_all(Path("ansible/manifests/milestone2-app.yml").read_text())
        )
        deployment = next(doc for doc in documents if doc["kind"] == "Deployment")
        init_command = deployment["spec"]["template"]["spec"]["initContainers"][0][
            "command"
        ][2]
        with tempfile.TemporaryDirectory() as directory:
            volume = Path(directory)
            volume.chmod(0o770)
            marker = volume / "index.html"
            marker.write_text("existing persistent marker\n")
            subprocess.run(
                ["sh", "-c", init_command.replace("/data", str(volume))],
                check=True,
            )
            self.assertTrue(volume.stat().st_mode & stat.S_IROTH)
            self.assertTrue(volume.stat().st_mode & stat.S_IXOTH)
            self.assertEqual(marker.read_text(), "existing persistent marker\n")

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
        "ansible/playbooks/validate_test_app.yml",
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

    def test_full_validation_runs_cluster_checks_before_app_checks(self):
        plays = yaml.safe_load(Path("ansible/playbooks/validate.yml").read_text())
        self.assertEqual(
            [play["ansible.builtin.import_playbook"] for play in plays],
            ["validate_cluster.yml", "validate_test_app.yml"],
        )

    def test_cluster_validation_needs_no_test_application(self):
        text = Path("ansible/playbooks/validate_cluster.yml").read_text()
        self.assertNotIn("milestone2", text)

    def test_validation_reaches_the_app_over_the_private_node_port(self):
        requests = [
            task["ansible.builtin.uri"]
            for task in self._tasks("ansible/playbooks/validate_test_app.yml")
            if "ansible.builtin.uri" in task
        ]
        self.assertEqual([request["status_code"] for request in requests], [200, 404])
        for request in requests:
            self.assertIn("node_internal_ip", request["url"])
            self.assertIn("{{ traefik_http_node_port }}", request["url"])
        self.assertEqual(requests[0]["headers"], {"Host": "milestone2.local"})
        self.assertNotIn("headers", requests[1])

    def test_app_validation_asserts_gateway_route_and_volume_status(self):
        tasks = self._tasks("ansible/playbooks/validate_test_app.yml")
        by_name = {task["name"]: task for task in tasks}
        self.assertIn(
            "--for=condition=Programmed gateway/milestone2-gateway",
            by_name["Wait for the Gateway to be programmed"]["ansible.builtin.command"],
        )
        route = by_name["Require the HTTPRoute to be accepted"]
        self.assertIn('@.type=="Accepted"', route["ansible.builtin.command"])
        self.assertEqual(route["until"], 'validate_route_accepted.stdout == "True"')
        pvc = by_name["Require the application PVC to be bound"]
        self.assertEqual(pvc["failed_when"], 'validate_pvc_phase.stdout != "Bound"')

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
        app_tasks = self._tasks("ansible/playbooks/validate_test_app.yml")
        self.assertEqual(app_tasks[0]["name"], "Wait for the test application after restart")


if __name__ == "__main__":
    unittest.main()
