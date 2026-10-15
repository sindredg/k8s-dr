import io
import threading
import unittest
from unittest import mock

from scripts import capture_probe as module


def _outcome(fn, took):
    log = io.StringIO()
    with mock.patch.object(module.time, "monotonic", side_effect=[0.0, took]):
        module.run("probe", fn, log, threading.Lock())
    _, name, outcome, seconds = log.getvalue().rstrip("\n").split("\t")
    return name, outcome, seconds


class OutcomeTests(unittest.TestCase):
    def test_records_a_fast_success(self):
        self.assertEqual(_outcome(lambda: "200", 0.3), ("probe", "200", "0.300"))

    def test_marks_a_slow_success_as_slow(self):
        self.assertEqual(_outcome(lambda: "200", module.SLOW)[1], "slow")

    def test_keeps_a_failure_status_even_when_slow(self):
        self.assertEqual(_outcome(lambda: "http-502", module.SLOW + 1)[1], "http-502")

    def test_records_an_exception_as_an_error(self):
        def boom():
            raise TimeoutError

        self.assertEqual(_outcome(boom, 0.1)[1], "error-TimeoutError")


class LsRemoteTests(unittest.TestCase):
    def test_sends_the_password_on_stdin_not_in_arguments(self):
        result = mock.Mock(returncode=0, stdout="abc\trefs/heads/main\n")
        with mock.patch.object(module.subprocess, "run", return_value=result) as run:
            self.assertEqual(module.ls_remote("https://user@host/a/b.git", "secret"), "200")
        args, kwargs = run.call_args
        self.assertNotIn("secret", " ".join(args[0]))
        self.assertEqual(kwargs["input"], "secret\n")

    def test_fails_on_an_empty_answer(self):
        result = mock.Mock(returncode=0, stdout="")
        with mock.patch.object(module.subprocess, "run", return_value=result):
            self.assertEqual(module.ls_remote("https://user@host/a/b.git", "x"), "git-exit-0")


if __name__ == "__main__":
    unittest.main()
