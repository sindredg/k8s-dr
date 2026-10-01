from pathlib import Path
import unittest

import yaml

DEPLOY = Path("deploy")
METADATA_SERVER = "169.254.169.254/32"


def _documents(path):
    return [document for document in yaml.safe_load_all(path.read_text()) if document]


class GiteaAccessTests(unittest.TestCase):
    def test_anonymous_visitors_must_sign_in(self):
        (release,) = _documents(DEPLOY / "apps/gitea/release.yaml")
        service = release["spec"]["values"]["gitea"]["config"]["service"]
        self.assertIs(service["REQUIRE_SIGNIN_VIEW"], True)
        self.assertIs(service["DISABLE_REGISTRATION"], True)


class TraefikEgressTests(unittest.TestCase):
    def setUp(self):
        (self.policy,) = _documents(DEPLOY / "infrastructure/traefik/network-policies.yaml")

    def test_policy_is_applied_by_the_infrastructure_kustomization(self):
        (infrastructure,) = _documents(DEPLOY / "infrastructure/kustomization.yaml")
        self.assertIn("traefik", infrastructure["resources"])
        (traefik,) = _documents(DEPLOY / "infrastructure/traefik/kustomization.yaml")
        self.assertEqual(traefik["resources"], ["network-policies.yaml"])

    def test_policy_restricts_egress_only_for_every_pod(self):
        self.assertEqual(self.policy["metadata"]["namespace"], "traefik")
        self.assertEqual(self.policy["spec"]["podSelector"], {})
        # An Ingress policy type would drop public traffic to the host ports.
        self.assertEqual(self.policy["spec"]["policyTypes"], ["Egress"])
        self.assertNotIn("ingress", self.policy["spec"])

    def test_only_listed_destinations_are_allowed(self):
        flows = set()
        for rule in self.policy["spec"]["egress"]:
            # A rule without peers allows any address.
            for peer in rule.get("to", [None]):
                if peer is None:
                    other = "any"
                else:
                    namespace = peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
                    labels = peer["podSelector"]["matchLabels"]
                    other = f"{namespace}/{labels.get('app.kubernetes.io/name', labels.get('k8s-app'))}"
                for port in rule["ports"]:
                    flows.add((other, port["protocol"], port["port"]))
        self.assertEqual(
            flows,
            {
                ("kube-system/kube-dns", "UDP", 53),
                ("kube-system/kube-dns", "TCP", 53),
                ("any", "TCP", 6443),
                ("gitea/gitea", "TCP", 3000),
            },
        )


class MetadataServerTests(unittest.TestCase):
    DIRECTORIES = {
        "cert-manager": DEPLOY / "infrastructure/cert-manager",
        "monitoring": DEPLOY / "monitoring",
    }

    def test_namespaces_cannot_reach_the_metadata_server(self):
        for namespace, directory in self.DIRECTORIES.items():
            with self.subTest(namespace=namespace):
                (kustomization,) = _documents(directory / "kustomization.yaml")
                self.assertIn("network-policies.yaml", kustomization["resources"])
                (policy,) = _documents(directory / "network-policies.yaml")
                self.assertEqual(policy["metadata"]["namespace"], namespace)
                self.assertEqual(
                    policy["spec"],
                    {
                        "podSelector": {},
                        "policyTypes": ["Egress"],
                        "egress": [{"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": [METADATA_SERVER]}}]}],
                    },
                )

    def test_only_the_backup_job_is_allowed_the_metadata_server(self):
        allowed = set()
        for path in DEPLOY.rglob("network-policies.yaml"):
            for policy in _documents(path):
                for rule in policy["spec"].get("egress", []):
                    for peer in rule.get("to", []):
                        if peer.get("ipBlock", {}).get("cidr") == METADATA_SERVER:
                            pods = policy["spec"]["podSelector"].get("matchLabels", {})
                            allowed.add((policy["metadata"]["namespace"], pods.get("app.kubernetes.io/name")))
        self.assertEqual(allowed, {("gitea", "gitea-backup")})


class NodeScopeTests(unittest.TestCase):
    def test_nodes_do_not_get_the_cloud_platform_scope(self):
        module = Path("infra/modules/regional_cluster/main.tf").read_text()
        self.assertNotIn("cloud-platform", module)
        self.assertIn('"control-plane" = []', module)
        self.assertIn('"worker"        = ["storage-rw"]', module)
        self.assertIn("scopes = local.node_scopes[each.key]", module)


if __name__ == "__main__":
    unittest.main()
