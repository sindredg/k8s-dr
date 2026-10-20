# The restore failed with status 141 after the digests matched

Observed on 2026-10-19 in the `Recover` run 39218739238, which the watchdog started during the first unattended drill of [milestone 11](../worklogs/11-unattended-drills.md) (issue #126).

## Symptom

The `restore` stage failed. The restore Job logged three lines, the last two in the same second:

```text
2026-10-19T08:10:08Z restoring primary/20261019T072201Z; backup age at restore start 2887 seconds
2026-10-19T08:10:35Z digests match the manifest
2026-10-19T08:10:35Z restore failed with status 141 before any change
```

Status 141 is 128 plus signal 13, SIGPIPE.

## Cause

Confirmed by a local reproduction. `deploy/apps/gitea/backup/restore.sh` checked the dump with this line, under `set -o pipefail`:

```bash
age --decrypt -i "$KEY" "${STAGING}/postgresql.dump.age" | pg_restore --list >/dev/null
```

`pg_restore --list` reads the header and the table of contents of a custom-format dump and exits. It does not read the table data that follows. When more data follows than the pipe holds, the next write by `age` fails, `age` exits on SIGPIPE, and `pipefail` fails the script.

Evidence:

- The failure came in the same second as the digest check. The next check reads the 2.15 GB Gitea archive and cannot finish that fast.
- The dump grew from 398,088 bytes in set `20261017T230701Z` to 429,443 bytes in set `20261017T232201Z`, the first set after the drill dataset added 200 issues. The failed run restored a set of 429,775 bytes. The last `Recover` run before the drill started at 18:25Z on 2026-10-17, before the [dataset was loaded](../worklogs/11-unattended-drills.md) from 23:01Z, so no recovery had restored a set with the dataset.
- Local reproduction on 2026-10-19 with PostgreSQL 16.15 and age from Ubuntu 24.04. The backup image runs PostgreSQL 18.6, so the versions differ. Two databases had the same schema of 121 tables. A dump with 10 short rows of data (237,877 bytes) passed the line in 3 of 3 runs. A dump with 200 rows of 1,280 characters (385,028 bytes) failed in 3 of 3 runs, with `age` exiting 141 and `pg_restore` exiting 0.
- The two other pipelines in the script read their input to the end. `age --decrypt | tar -tzf -` passed 3 of 3 runs on a 33.6 MB archive built as `deploy/apps/gitea/backup/backup.sh` builds it. `age --decrypt | pg_restore --dbname` restored the larger dump in 3 of 3 runs with all 200 rows.

Not confirmed: the exact size at which the line starts to fail. It depends on how much table data follows the table of contents and on how far `age` has written when `pg_restore` exits.

The restore failed before it changed anything, so the recovery cluster kept its empty Gitea, and the public name kept pointing at the stopped primary. The watchdog starts nothing after a failed automatic recovery, and the drill does not return after a failed recovery, so `git.sindrg.com` did not serve from the isolation at 07:31:05Z until the `Return` run 39264176899 started the primary at 13:58:23Z.

## Fix

`restore.sh` decrypts the dump once to the staging volume. `pg_restore --list` reads that file, and the database restore reads the same file. No pipeline ends in `pg_restore`. The staging volume has a limit of 4 GiB and holds the encrypted archive of 2.15 GB, so a second copy of a dump under 1 MB fits.

## Verify

Local, 2026-10-19:

- `test_reads_the_dump_from_a_decrypted_file` in `tests/test_services.py` failed against the script before the fix and passes after it.
- The fixed lines, copied from the script and run with the real tools above, passed 3 of 3 runs against the dump that failed 3 of 3 before, and restored all 200 rows.

The fix has not run live. The next `Recover` run or drill from `main` after the merge exercises it.
