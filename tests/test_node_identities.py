from pathlib import Path
import re
import unittest

INFRA = Path("infra")


def _read(path):
    return (INFRA / path).read_text()


def _block(text, header):
    start = text.index(header)
    depth = 0
    for index in range(text.index("{", start), len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[start : index + 1]
    raise AssertionError(f"unclosed block: {header}")


class NodeIdentityOwnerTests(unittest.TestCase):
    """Decision 0013: infra/shared owns node accounts and backup grants."""

    def test_regional_code_creates_no_service_account_or_iam(self):
        for path in ["modules/regional_cluster/main.tf", "primary/main.tf", "recovery/main.tf"]:
            text = _read(path)
            with self.subTest(path=path):
                self.assertNotRegex(text, r'resource\s+"google_service_account')
                self.assertNotRegex(text, r'resource\s+"google_[a-z_]*iam_')

    def test_module_attaches_the_looked_up_accounts(self):
        module = _read("modules/regional_cluster/main.tf")
        self.assertIn('data "google_service_account" "node"', module)
        self.assertIn("email  = data.google_service_account.node[each.key].email", module)

    def test_primary_forgets_the_moved_objects_without_destroying_them(self):
        primary = _read("primary/main.tf")
        removed = {}
        for match in re.finditer(r"^removed \{", primary, re.M):
            block = _block(primary[match.start() :], "removed {")
            (address,) = re.findall(r"from = (\S+)", block)
            removed[address] = re.search(r"destroy\s*=\s*false", block) is not None
        self.assertEqual(
            removed,
            {
                "module.primary_cluster.google_service_account.node": True,
                "google_service_account_iam_member.admin_can_use_node": True,
            },
        )

    def test_fencing_by_disabling_survives_a_shared_apply(self):
        account = _block(_read("shared/main.tf"), 'resource "google_service_account" "node"')
        self.assertRegex(account, r"ignore_changes\s*=\s*\[disabled\]")

    def test_each_worker_writes_only_under_its_own_prefix(self):
        shared = _read("shared/main.tf")
        writer = _block(shared, 'resource "google_storage_bucket_iam_member" "backup_writer"')
        self.assertIn("for_each = var.clusters", writer)
        self.assertIn('role     = "roles/storage.objectCreator"', writer)
        self.assertIn('member   = google_service_account.node["${each.key}/worker"].member', writer)
        self.assertIn("/objects/${each.key}/", writer)
        reader = _block(shared, 'resource "google_storage_bucket_iam_member" "backup_reader"')
        self.assertIn('role     = "roles/storage.objectViewer"', reader)
        self.assertIn('member   = google_service_account.node["${each.key}/worker"].member', reader)

    def test_shared_clusters_match_the_regional_prefixes(self):
        variables = _block(_read("shared/variables.tf"), 'variable "clusters"')
        defaults = dict(re.findall(r'^\s+(\w+)\s+=\s+"([a-z0-9-]+)"$', variables, re.M))
        for cluster in ["primary", "recovery"]:
            example = _read(f"{cluster}/terraform.tfvars.example")
            (prefix,) = re.findall(r'^name_prefix\s+=\s+"([^"]+)"', example, re.M)
            with self.subTest(cluster=cluster):
                self.assertEqual(defaults.get(cluster), prefix)


if __name__ == "__main__":
    unittest.main()
