#!/usr/bin/env python3
"""Record a recovery drill's stage timeline and compute its data loss.

The drill writes two local logs under .drill/ on the operator machine:

  writes.tsv    one line per acknowledged write check: UTC time, commit, URL
  timeline.tsv  one line per stage event: UTC time, stage, event, note

Both stay outside the failed region and outside Git. This script appends
stage events, prints the timeline as a Markdown table, and reports observed
data loss from the write log. See decision 0002 and
docs/runbooks/regional-recovery.md.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

DRILL_DIR = Path(".drill")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SET_FORMAT = "%Y%m%dT%H%M%SZ"
STAGES = (
    "detection",
    "provisioning",
    "bootstrap",
    "flux-reconcile",
    "restore",
    "verification",
    "dns-cutover",
    "probe-recovery",
)


@dataclass(frozen=True)
class Write:
    acknowledged: datetime
    sha: str
    url: str


def parse_time(text: str, fmt: str = TIME_FORMAT) -> datetime:
    return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)


def format_duration(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s"


def read_writes(path: Path) -> list[Write]:
    writes = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError(f"{path}:{number}: expected time, commit, and URL")
        writes.append(Write(parse_time(fields[0]), fields[1], fields[2]))
    return sorted(writes, key=lambda write: write.acknowledged)


def data_loss(writes: list[Write], restored_sha: str, isolated_at: datetime) -> dict:
    """Compare the restored HEAD with the writes acknowledged before isolation.

    Writes after isolation went to the recovery cluster, so they are ignored:
    a new recovery push must not hide a primary write that the restore lost.
    """
    primary = [write for write in writes if write.acknowledged <= isolated_at]
    if not primary:
        raise ValueError("the write log holds no write acknowledged before isolation")
    restored = next((write for write in primary if write.sha == restored_sha), None)
    if restored is None:
        raise ValueError(
            f"restored HEAD {restored_sha} is not a write acknowledged before isolation; "
            "record it before any write to the recovery copy"
        )
    last = primary[-1]
    return {
        "last": last,
        "restored": restored,
        "loss": last.acknowledged - restored.acknowledged,
        "lost": [write for write in primary if write.acknowledged > restored.acknowledged],
    }


def read_events(path: Path) -> list[tuple[datetime, str, str, str]]:
    events = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise ValueError(f"{path}:{number}: expected time, stage, event, and note")
        events.append((parse_time(fields[0]), fields[1], fields[2], fields[3]))
    return events


def timeline_table(events: list[tuple[datetime, str, str, str]]) -> str:
    """One row per stage in the order the stages started."""
    rows: dict[str, dict] = {}
    for time, stage, event, note in events:
        row = rows.setdefault(stage, {"start": None, "end": None, "notes": []})
        if event in ("start", "end"):
            # A retried stage keeps its first start and its last end.
            if event == "start" and row["start"] is None:
                row["start"] = time
            if event == "end":
                row["end"] = time
        if note:
            row["notes"].append(f"{time.strftime('%H:%M:%S')} {note}")
    lines = ["| Stage | Start (UTC) | End (UTC) | Duration | Notes |", "| --- | --- | --- | --- | --- |"]
    for stage, row in rows.items():
        start = row["start"].strftime(TIME_FORMAT) if row["start"] else "not recorded"
        end = row["end"].strftime(TIME_FORMAT) if row["end"] else "not recorded"
        duration = format_duration(row["end"] - row["start"]) if row["start"] and row["end"] else ""
        lines.append(f"| {stage} | {start} | {end} | {duration} | {'; '.join(row['notes'])} |")
    return "\n".join(lines)


def append_event(path: Path, stage: str, event: str, note: str, now: datetime) -> str:
    if "\t" in note or "\n" in note:
        raise ValueError("a note must be one line without tabs")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = "\t".join((now.strftime(TIME_FORMAT), stage, event, note))
    with path.open("a", encoding="utf-8") as log:
        log.write(line + "\n")
    return line


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--drill-dir", type=Path, default=DRILL_DIR)
    commands = parser.add_subparsers(dest="command", required=True)

    stage = commands.add_parser("stage", help="Record the start or end of a stage, or a note in it.")
    stage.add_argument("stage", choices=STAGES)
    stage.add_argument("event", choices=("start", "end", "note"))
    stage.add_argument("--note", default="", help="A failure, retry, or manual action.")

    commands.add_parser("timeline", help="Print the stage timeline as a Markdown table.")

    rpo = commands.add_parser("rpo", help="Report observed data loss from the write log.")
    rpo.add_argument("--restored-sha", required=True, help="HEAD that check-fixtures reports after the restore.")
    rpo.add_argument("--isolated-at", required=True, help="UTC isolation time, such as 2026-10-10T12:00:00Z.")
    rpo.add_argument("--backup-set", help="Restored set name, such as 20261010T110701Z.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "stage":
            if args.event == "note" and not args.note:
                raise ValueError("a note event needs --note")
            print(append_event(
                args.drill_dir / "timeline.tsv", args.stage, args.event, args.note,
                datetime.now(timezone.utc),
            ))
        elif args.command == "timeline":
            print(timeline_table(read_events(args.drill_dir / "timeline.tsv")))
        else:
            isolated_at = parse_time(args.isolated_at)
            result = data_loss(read_writes(args.drill_dir / "writes.tsv"), args.restored_sha, isolated_at)
            last, restored = result["last"], result["restored"]
            print(f"Last acknowledged primary write: {last.acknowledged.strftime(TIME_FORMAT)} {last.sha}")
            print(f"Restored HEAD acknowledged:      {restored.acknowledged.strftime(TIME_FORMAT)} {restored.sha}")
            print(f"Observed data loss:              {format_duration(result['loss'])}")
            print(f"Acknowledged writes lost:        {len(result['lost'])}")
            for write in result["lost"]:
                print(f"  {write.acknowledged.strftime(TIME_FORMAT)} {write.sha}")
            if args.backup_set:
                window = isolated_at - parse_time(args.backup_set, SET_FORMAT)
                print(f"Potential loss window:           {format_duration(window)} (backup age at isolation)")
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
