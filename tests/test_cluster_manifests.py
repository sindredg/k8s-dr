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

    def test_validation_playbook_contains_only_read_only_kubectl_commands(self):
        plays = yaml.safe_load(Path("ansible/playbooks/validate.yml").read_text())
        tasks = plays[0]["tasks"]
        commands = [task["ansible.builtin.command"] for task in tasks]
        for command in commands:
            self.assertRegex(command, r"^kubectl .*\bget\b")
        self.assertTrue(all(task["changed_when"] is False for task in tasks))


if __name__ == "__main__":
    unittest.main()
