import gzip
import unittest
import urllib.error

from scripts import check_pins as module

PINS = {
    "kubernetes_minor": "v1.36",
    "kubernetes_deb_version": "1.36.2-2.1",
    "containerd_deb_version": "2.2.1-0ubuntu1~24.04.3",
    "helm_version": "3.22.0",
    "calico_version": "3.32.2",
    "gateway_api_version": "1.6.1",
    "traefik_chart_version": "41.6.0",
    "local_path_provisioner_version": "0.0.36",
}

KUBERNETES_INDEX = "\n\n".join(
    f"Package: {package}\nVersion: 1.36.2-2.1\nArchitecture: amd64"
    for package in ("kubelet", "kubeadm", "kubectl")
)


def packages_index(*versions):
    return "\n\n".join(f"Package: containerd\nVersion: {version}" for version in versions)


class FakeUpstream:
    def __init__(self, responses=None, missing=()):
        self.responses = responses or {}
        self.missing = set(missing)
        self.requests = []

    def __call__(self, url, method="GET"):
        self.requests.append((method, url))
        if url in self.missing:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return self.responses.get(url, b"")


class ParserTests(unittest.TestCase):
    def test_debian_versions_match_whole_package_names(self):
        index = (
            "Package: containerd\nVersion: 1.0\n\n"
            "Package: containerd-tools\nVersion: 2.0\n\n"
            "Package: containerd\nDescription: runtime\n more text\nVersion: 3.0"
        )
        self.assertEqual(module.debian_package_versions(index, "containerd"), {"1.0", "3.0"})

    def test_chart_versions_reads_one_chart(self):
        index = "entries:\n  traefik:\n    - version: 41.6.0\n  other:\n    - version: 1.0.0\n"
        self.assertEqual(module.chart_versions(index, "traefik"), {"41.6.0"})
        self.assertEqual(module.chart_versions(index, "missing"), set())


class CheckTests(unittest.TestCase):
    def test_kubernetes_revision_must_exist_for_every_package(self):
        url = "https://pkgs.k8s.io/core:/stable:/v1.36/deb/Packages"
        index = KUBERNETES_INDEX.replace("Package: kubectl\nVersion: 1.36.2-2.1", "Package: kubectl\nVersion: 1.36.1-1.1")
        result = module.check_kubernetes_packages(PINS, FakeUpstream({url: index.encode()}))
        self.assertFalse(result.ok)
        self.assertIn("kubectl", result.detail)
        self.assertNotIn("kubelet", result.detail)

    def test_kubernetes_revision_passes_when_published(self):
        url = "https://pkgs.k8s.io/core:/stable:/v1.36/deb/Packages"
        result = module.check_kubernetes_packages(PINS, FakeUpstream({url: KUBERNETES_INDEX.encode()}))
        self.assertTrue(result.ok)

    def test_containerd_passes_from_any_pocket(self):
        security = "http://archive.ubuntu.com/ubuntu/dists/noble-security/main/binary-amd64/Packages.gz"
        responses = {
            url: gzip.compress(packages_index("1.7.12").encode())
            for url in (
                f"http://archive.ubuntu.com/ubuntu/dists/{pocket}/main/binary-amd64/Packages.gz"
                for pocket in module.UBUNTU_POCKETS
            )
        }
        responses[security] = gzip.compress(packages_index(PINS["containerd_deb_version"]).encode())
        result = module.check_containerd(PINS, FakeUpstream(responses))
        self.assertTrue(result.ok)
        self.assertIn("noble-security", result.detail)

    def test_superseded_containerd_fails_and_lists_available_versions(self):
        responses = {
            f"http://archive.ubuntu.com/ubuntu/dists/{pocket}/main/binary-amd64/Packages.gz": gzip.compress(
                packages_index("2.2.1-0ubuntu1~24.04.4").encode()
            )
            for pocket in module.UBUNTU_POCKETS
        }
        result = module.check_containerd(PINS, FakeUpstream(responses))
        self.assertFalse(result.ok)
        self.assertIn("2.2.1-0ubuntu1~24.04.4", result.detail)

    def test_missing_file_fails_without_raising(self):
        url = "https://example.invalid/file.yaml"
        result = module.check_url("File", url, FakeUpstream(missing={url}))
        self.assertFalse(result.ok)
        self.assertIn("404", result.detail)

    def test_file_checks_use_head_requests_for_pinned_urls(self):
        upstream = FakeUpstream()
        for name, url in module.artifact_urls(PINS).items():
            self.assertTrue(module.check_url(name, url, upstream).ok)
        self.assertTrue(all(method == "HEAD" for method, _ in upstream.requests))
        urls = [url for _, url in upstream.requests]
        self.assertIn("https://get.helm.sh/helm-v3.22.0-linux-amd64.tar.gz", urls)
        self.assertTrue(all("latest" not in url and "master" not in url for url in urls))

    def test_every_pinned_version_variable_is_checked(self):
        # A new version pin in group_vars must be added to the checks.
        class RecordingPins(dict):
            def __init__(self, values):
                super().__init__(values)
                self.read = set()

            def __getitem__(self, key):
                self.read.add(key)
                return super().__getitem__(key)

        variables = module.yaml.safe_load(module.DEFAULT_VARIABLES.read_text())
        pins = RecordingPins(variables)
        module.run_checks(pins, FakeUpstream())
        versions = {name for name in variables if name.endswith(("_version", "_minor"))}
        # kubernetes_version is the kubeadm target; the package revision covers it.
        self.assertEqual(versions - pins.read, {"kubernetes_version"})

if __name__ == "__main__":
    unittest.main()
