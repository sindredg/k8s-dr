from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from scripts import drill_report as module


def _write(time, sha, url="https://git.sindrg.com"):
    return module.Write(module.parse_time(time), sha, url)


WRITES = [
    _write("2026-10-10T10:55:00Z", "aaa"),
    _write("2026-10-10T11:05:00Z", "bbb"),
    _write("2026-10-10T11:10:00Z", "ccc"),
    _write("2026-10-10T11:15:00Z", "ddd"),
    # Pushed to the recovery cluster after the restore.
    _write("2026-10-10T12:40:00Z", "eee", "https://git-dr.sindrg.com"),
]
ISOLATED = module.parse_time("2026-10-10T11:17:00Z")


class DataLossTests(unittest.TestCase):
    def test_reports_the_writes_after_the_restored_head_as_lost(self):
        result = module.data_loss(WRITES, "bbb", ISOLATED)
        self.assertEqual(result["last"].sha, "ddd")
        self.assertEqual(result["loss"], timedelta(minutes=10))
        self.assertEqual([write.sha for write in result["lost"]], ["ccc", "ddd"])

    def test_a_recovery_push_does_not_hide_a_lost_primary_write(self):
        # The newest commit after recovery is eee, yet ddd is still lost.
        result = module.data_loss(WRITES, "ccc", ISOLATED)
        self.assertEqual(result["last"].sha, "ddd")
        self.assertEqual([write.sha for write in result["lost"]], ["ddd"])

    def test_no_loss_when_the_last_write_was_restored(self):
        result = module.data_loss(WRITES, "ddd", ISOLATED)
        self.assertEqual(result["loss"], timedelta(0))
        self.assertEqual(result["lost"], [])

    def test_rejects_a_head_recorded_after_a_recovery_write(self):
        with self.assertRaisesRegex(ValueError, "before any write to the recovery copy"):
            module.data_loss(WRITES, "eee", ISOLATED)

    def test_rejects_an_empty_log(self):
        with self.assertRaisesRegex(ValueError, "no write acknowledged before isolation"):
            module.data_loss([], "aaa", ISOLATED)

    def test_reads_the_log_that_write_check_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "writes.tsv"
            log.write_text(
                "2026-10-10T11:05:00Z\tbbb\thttps://git.sindrg.com\n"
                "2026-10-10T10:55:00Z\taaa\thttps://git.sindrg.com\n"
            )
            self.assertEqual([write.sha for write in module.read_writes(log)], ["aaa", "bbb"])
            log.write_text("2026-10-10T11:05:00Z bbb\n")
            with self.assertRaisesRegex(ValueError, "expected time, commit, and URL"):
                module.read_writes(log)


class TimelineTests(unittest.TestCase):
    def test_stage_events_render_as_a_table(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "drill" / "timeline.tsv"
            module.append_event(log, "restore", "start", "", module.parse_time("2026-10-10T12:00:00Z"))
            module.append_event(
                log, "restore", "note", "first Job failed; reran", module.parse_time("2026-10-10T12:03:00Z")
            )
            module.append_event(log, "restore", "end", "", module.parse_time("2026-10-10T12:10:30Z"))
            module.append_event(log, "verification", "start", "", module.parse_time("2026-10-10T12:11:00Z"))
            table = module.timeline_table(module.read_events(log))
        self.assertIn(
            "| restore | 2026-10-10T12:00:00Z | 2026-10-10T12:10:30Z | 0h 10m 30s | "
            "12:03:00 first Job failed; reran |",
            table,
        )
        self.assertIn("| verification | 2026-10-10T12:11:00Z | not recorded |  |  |", table)

    def test_a_retried_stage_keeps_its_first_start(self):
        events = [
            (module.parse_time("2026-10-10T12:00:00Z"), "bootstrap", "start", ""),
            (module.parse_time("2026-10-10T12:20:00Z"), "bootstrap", "start", "retry"),
            (module.parse_time("2026-10-10T12:45:00Z"), "bootstrap", "end", ""),
        ]
        self.assertIn("| 0h 45m 00s |", module.timeline_table(events))

    def test_notes_must_fit_one_field(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                module.append_event(
                    Path(directory) / "timeline.tsv", "restore", "note", "a\tb",
                    module.parse_time("2026-10-10T12:00:00Z"),
                )


class WriteLogContractTests(unittest.TestCase):
    def test_write_check_appends_the_fields_the_report_reads(self):
        playbook = Path("ansible/playbooks/write_check.yml").read_text()
        self.assertIn(
            '{{ write_check_acknowledged }}\\t{{ write_check_sha.stdout }}\\t{{ git_url }}', playbook
        )
        makefile = Path("Makefile").read_text()
        self.assertIn("-e write_check_log=", makefile)
        self.assertIn(".drill/", Path(".gitignore").read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
