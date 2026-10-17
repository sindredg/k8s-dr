from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

from scripts import dataset as module

FIXTURES = yaml.safe_load(module.FIXTURES.read_text())
IDENTITY = FIXTURES["fixture_commit"]["author"]
# Two repositories of three parts of 5000 bytes, and four issues.
SIZES = (2, 3, 5000, 4)


class FakeGitea(module.Gitea):
    """An endpoint that stores branch heads and issues, and records each push."""

    def __init__(self):
        super().__init__("https://git.example.test", FIXTURES["fixture_user"], "secret")
        self.repositories: dict[str, str] = {}
        self.issues: dict[str, int] = {}
        self.pushes: list[tuple[str, str]] = []
        self.posts: list[str] = []

    def api(self, method, path, body=None):
        if method == "POST":
            self.posts.append(path)
        name = path.split("/")[3] if path.startswith("/repos/") else None
        if method == "GET" and path.endswith("issues?state=all&type=issues&limit=1"):
            return 200, {"X-Total-Count": str(self.issues.get(name, 0))}, []
        if method == "GET":
            return (200, {}, {}) if name in self.repositories else (404, {}, None)
        if path == "/user/repos":
            self.repositories[body["name"]] = ""
            return 201, {}, {}
        if path.endswith("/issues"):
            self.issues[name] = self.issues.get(name, 0) + 1
            return 201, {}, {}
        raise AssertionError(path)

    def head(self, repository, branch):
        return self.repositories.get(repository, "")

    def git(self, *args, cwd=None):
        self.assertPassword()
        _, _, url, refspec = args
        sha = refspec.split(":")[0]
        # A real push fails unless the pushed commit exists in the clone.
        subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=cwd, check=True)
        repository = url.rsplit("/", 1)[-1].removesuffix(".git")
        self.repositories[repository] = sha
        self.pushes.append((repository, sha))
        return ""

    def assertPassword(self):
        assert self.password == "secret"


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.gitea = FakeGitea()
        self.output: list[str] = []
        self.errors: list[str] = []

    def main(self, command):
        def record(*args, **kwargs):
            (self.errors if kwargs.get("file") is not None else self.output).append(" ".join(map(str, args)))
        with mock.patch("builtins.print", side_effect=record):
            return module.main([command], gitea=self.gitea, sizes=SIZES)

    def test_the_computed_shas_are_the_shas_that_git_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            built = module.build_repository(Path(directory), "dataset-01", IDENTITY, "main", 3, 5000)
        self.assertEqual(built, module.expected_commits("dataset-01", IDENTITY, 3, 5000))
        self.assertEqual(len(set(built)), 3)

    def test_each_repository_and_part_has_its_own_content(self):
        first = b"".join(module.part_bytes("dataset-01", 1, 100))
        self.assertEqual(first, b"".join(module.part_bytes("dataset-01", 1, 100)))
        self.assertNotEqual(first, b"".join(module.part_bytes("dataset-02", 1, 100)))
        self.assertNotEqual(first, b"".join(module.part_bytes("dataset-01", 2, 100)))

    def test_a_part_is_split_into_chunks_of_the_exact_size(self):
        size = module.CHUNK * 2 + 7
        self.assertEqual(sum(len(chunk) for chunk in module.part_bytes("dataset-01", 1, size)), size)

    def test_the_full_dataset_is_two_gibibytes(self):
        self.assertEqual(module.REPOSITORIES * module.COMMITS * module.PART_SIZE, 2 * 1024 ** 3)

    def test_load_creates_and_pushes_every_part_once(self):
        self.assertEqual(self.main("load"), 0, self.errors)
        self.assertEqual(sorted(self.gitea.repositories), ["dataset-01", "dataset-02"])
        self.assertEqual(len(self.gitea.pushes), 6)
        self.assertEqual(self.gitea.issues, {"dataset-01": 4})
        self.assertEqual(self.main("check"), 0, self.errors)

    def test_a_second_load_pushes_nothing(self):
        self.assertEqual(self.main("load"), 0, self.errors)
        pushes, posts = len(self.gitea.pushes), len(self.gitea.posts)
        self.assertEqual(self.main("load"), 0, self.errors)
        self.assertEqual(len(self.gitea.pushes), pushes)
        self.assertEqual(len(self.gitea.posts), posts)

    def test_an_interrupted_load_resumes_after_the_last_pushed_part(self):
        shas = module.expected_commits("dataset-01", IDENTITY, 3, 5000)
        self.gitea.repositories["dataset-01"] = shas[0]
        self.gitea.issues["dataset-01"] = 3
        self.assertEqual(self.main("load"), 0, self.errors)
        self.assertEqual([sha for name, sha in self.gitea.pushes if name == "dataset-01"], shas[1:])
        self.assertEqual(self.gitea.issues["dataset-01"], 4)

    def test_load_refuses_a_branch_that_is_not_dataset_history(self):
        self.gitea.repositories["dataset-01"] = "f" * 40
        self.assertEqual(self.main("load"), 1)
        self.assertIn("not a dataset commit", self.errors[-1])
        self.assertEqual(self.gitea.pushes, [])

    def test_load_refuses_to_push_commits_that_differ_from_the_computed_shas(self):
        with mock.patch.object(module, "build_repository", return_value=["e" * 40] * 3):
            self.assertEqual(self.main("load"), 1)
        self.assertIn("do not match the computed SHAs", self.errors[-1])
        self.assertEqual(self.gitea.pushes, [])

    def test_check_fails_on_a_missing_or_partial_repository(self):
        self.assertEqual(self.main("load"), 0, self.errors)
        shas = module.expected_commits("dataset-02", IDENTITY, 3, 5000)
        for head in (shas[1], ""):
            with self.subTest(head=head):
                self.gitea.repositories["dataset-02"] = head
                self.assertEqual(self.main("check"), 1)
                self.assertIn("dataset-02", self.errors[-1])

    def test_check_fails_on_a_missing_issue(self):
        self.assertEqual(self.main("load"), 0, self.errors)
        self.gitea.issues["dataset-01"] = 3
        self.assertEqual(self.main("check"), 1)
        self.assertIn("3 issues, expected 4", self.errors[-1])

    def test_the_password_never_reaches_an_argument(self):
        gitea = module.Gitea("https://git.example.test", "user", "pa55word")
        with mock.patch.object(module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            gitea.git("ls-remote", "https://git.example.test/user/x.git")
        argv, kwargs = run.call_args
        self.assertNotIn("pa55word", " ".join(argv[0]))
        self.assertEqual(kwargs["input"], "pa55word\n")


if __name__ == "__main__":
    unittest.main()
