# Backups without downtime

Status: Complete. The gate passed on 2026-10-15; see [Validation](#validation). This file is the record of milestone 9.

## What was built

[Decision 0014](../decisions/0014-snapshot-backups.md) captures each backup from one LVM snapshot of the worker data volume while Gitea keeps running.

| Part | File | What it does |
| --- | --- | --- |
| Disk layout | `ansible/roles/worker_storage/tasks/main.yml` | Makes a blank data disk the volume group `k8sdr` with the ext4 logical volume `data` on 80% of it. Reuses an existing `k8sdr` volume group. Refuses a plain ext4 disk, another volume group, the boot disk, and a volume group with less than the snapshot reserve free. |
| Snapshot command | `ansible/roles/worker_storage/files/k8sdr-snapshot.sh`, installed as `/usr/local/sbin/k8sdr-snapshot` | `create` removes a leftover snapshot, creates `k8sdr/backup`, mounts it under a 0700 directory, and binds each claim's volume directory to `/var/lib/k8s-dr-backup/<namespace>`. A failed `create` removes what it made. `remove` undoes it. |
| Backup Pod | `deploy/apps/gitea/backup.yaml` | A privileged native sidecar `snapshot`, then `dump-postgresql` as 999, `archive-gitea` as 1000, and the main container `backup` as 1000 for the upload. No live volume is mounted. |
| Backup script | `deploy/apps/gitea/backup/backup.sh` | One subcommand per container. The manifest records `pause_seconds: 0` and `capture: "lvm-snapshot"`. |
| Restore identity | `deploy/apps/gitea/backup.yaml`, `deploy/apps/gitea/network-policies.yaml`, `deploy/apps/postgresql/network-policies.yaml`, `ansible/playbooks/templates/restore-job.yml.j2` | The restore Job runs as `gitea-restore`, which keeps the scale permission and the PostgreSQL access. The backup Role can only read the Gitea Deployment, and the backup Pod has no PostgreSQL access. |
| Shell lint | `Makefile`, `ansible/requirements.txt` | `make check` runs ShellCheck 0.11.0 on every tracked shell script. |
| Capture probe | `scripts/capture_probe.py`, `tests/test_capture_probe.py` | Added for the gate. Requests `/api/healthz` and runs `git ls-remote` every 0.5 seconds and logs each result, so a short stall during a capture shows up. |

The set format did not change, so `restore.sh` did not change.

## Local checks

All results below are local, from 2026-10-14 and 2026-10-15. None ran on a cluster.

| Check | Where | Result | Can it fail |
| --- | --- | --- | --- |
| `make check` | Local, before `5ddf412` | 208 tests `OK`; yamllint, ansible-lint, ShellCheck, and syntax checks pass | Ten edits that break the design each failed the matching test: a privileged capture container, a wrong claim, a wrong `PGDATA` major, a missing `HostToContainer`, a renamed volume group, no snapshot reserve, `vgcreate` on a non-blank disk, a write verb in the backup Role, PostgreSQL egress for the backup Pod, and a renamed script step |
| `make manifests` | Local, kubeconform 0.8.0 | `Summary: 146 resources found in 16 files - Valid: 146, Invalid: 0, Errors: 0, Skipped: 0` | Unchanged check |
| Storage role | Privileged `ubuntu:24.04` container, ansible-core 2.21.4, 1 GiB loop devices | Blank disk: `ok=40 changed=9 failed=0`, `data` 816 MiB, 204 MiB free. Rerun: `changed=0`. Plain ext4: fails with `Refusing unexpected filesystem 'ext4'`. Volume group without free space: fails with `has 0 of 1069547520 bytes free` | The two refusals are the failing cases |
| `k8sdr-snapshot` | Privileged `ubuntu:24.04` container; LVM commands replaced by stubs, real loop ext4 and mounts | 15 of 15 checks pass: create, binds, mount options and mode, create over a leftover, remove, repeated remove, missing claim, two matching directories, invalid name, two claims in one namespace, no claims, `lvcreate` failure, unknown command | The first run failed 9 checks because the file was not executable, and the mode check failed until the mount moved under a 0700 parent |
| `dump-postgresql` | Published backup image, amd64 under emulation; a data directory left by `SIGKILL` with 100 rows only in the write-ahead log | Exit 0. Restored into a fresh server: `300\|100` (all rows, and the 100 from the log). No `postmaster.pid` left | Bad recipients after the server started: exit 1, failure ping attempted, server stopped |
| `archive-gitea` | Same image, as 1000 | Exit 0; the archive decrypts and lists the files | A `failed` snapshot state: exit 1 before reading |
| `upload` | Same image, with stub `kubectl` and `curl` | Both archives, then `manifest.json`; digests and sizes match; `pause_seconds` 0; success ping | Not shown failing |
| `snapshot` sidecar | Same image, privileged, host `/proc` mounted | A failed create writes `failed`, stays up, and on `SIGTERM` exits 1 with a failure ping | `nsenter` into the host mount namespace failed with `Invalid argument` under amd64 emulation. A native arm64 `nsenter` with the same arguments succeeded, so the error is attributed to the emulator (hypothesis, not confirmed on the worker) |

Not checked locally: a real LVM snapshot (the local kernel has no device-mapper snapshot target), `nsenter` from the amd64 image, mount propagation into the capture containers, and the Pod on Kubernetes. The gate covers them.

## Live conversion of the primary data disk

Date: 2026-10-14. Runbook: [snapshot backups](../runbooks/snapshot-backups.md#convert-a-data-disk-from-the-earlier-layout). The primary worker had the plain ext4 layout from the milestone 8 bootstrap and no fixtures, so no restore was needed.

| Step | Command | Result |
| --- | --- | --- |
| Drain | `kubectl drain <worker> --ignore-daemonsets --delete-emptydir-data --timeout=300s` | `node/<worker> drained` |
| Erase | `umount`, `wipefs --all`, remove the `fstab` line | `2 bytes were erased at offset 0x00000438 (ext4): 53 ef`, then `blank` |
| Bootstrap, as first written | `make bootstrap FLUX_GIT_BRANCH=feat/snapshot-backups` | Worker `changed=8 failed=0`: the volume group, logical volume, filesystem, mount, and `k8sdr-snapshot` were created. Control plane `failed=1` at `Wait for Traefik`: `timed out waiting for the condition`. See deviation 1 |
| Uncordon, then bootstrap again | `kubectl uncordon <worker>`, `make bootstrap FLUX_GIT_BRANCH=feat/snapshot-backups` | `failed=0` on both nodes, worker `changed=0`, 214 s |
| Worker-only bootstrap | `make bootstrap BOOTSTRAP_ARGS='--limit worker'` | Only the worker in the recap, `changed=0 failed=0`; the delegated `Wait until the API lists the worker node` passed |
| Layout | `vgs k8sdr; lvs k8sdr; findmnt /var/lib/k8s-dr` | Volume group `<50.00g`, free `10.00g`; `data` `<40.00g`; `/dev/mapper/k8sdr-data ext4` |
| Validation | `make validate-cluster`, `make validate-services` | `failed=0` both |
| Flux source | `kubectl get gitrepository -n flux-system` | `stored artifact for revision 'feat/snapshot-backups@sha1:a0eb4e7…'` |
| DNS and fixtures | `make dns-set DNS_NAME=git DNS_ADDRESS=<address>`, `make create-fixtures`, `make check-fixtures` | Record set; `https://git.sindrg.com/api/healthz` returned `200`; both fixture runs `failed=0` |

### Deviations

| # | Step | Documented | Observed | Fix |
| --- | --- | --- | --- | --- |
| 1 | Convert, step 5 | `make bootstrap` with the worker still cordoned, uncordon after | The add-on play waits for Traefik, which runs only on the worker, so the bootstrap timed out after the storage play had succeeded | The runbook runs `make bootstrap BOOTSTRAP_ARGS='--limit worker'` while cordoned, then uncordons and runs the full bootstrap. The `bootstrap` target passes `BOOTSTRAP_ARGS` to `ansible-playbook` |

## Validation

Date: 2026-10-14, 22:29 to 23:27 UTC, on the primary, and the [dense probe](#dense-probe-across-four-more-captures) until 00:38 UTC on 2026-10-15. The deployed code is `main` at `74fc93e`. Until a bootstrap moved Flux to `main` between captures 1 and 2, it followed the pull request branch. Its head `5ddf412` has the same tree as `74fc93e` (`git rev-parse 5ddf412^{tree} 74fc93e^{tree}` prints the same hash twice). Capture 1 may have run from the earlier head `a0eb4e7`, which differs only outside `deploy/` and `ansible/`. The four captures below are consecutive scheduled runs; no scheduled run in that window failed.

Expected before the run, and why it proves the gate: if a capture paused Gitea, the write loop would log a failed push, the uptime check would report `false`, and the Gitea Pod would restart or be replaced. If the snapshot were not one consistent instant, or the dump or archive were broken, the restore or the fixture checks would fail.

The independent review found that these checks are too sparse to see a short stall. One push about every 68 seconds overlapped a 10 to 18 second capture only by chance, and the uptime check runs once a minute per region; its series repeats the latest result every 10 seconds. A [dense probe](#dense-probe-across-four-more-captures) over four more captures covers that gap.

### Captures under the write loop

`make write-loop WRITE_INTERVAL=60 DRILL_DIR=.drill/m9` acknowledged its first write at 22:29:08 and its last at 23:26:41 UTC, and was stopped during its 53rd run: 52 acknowledged writes, no `write check failed` line, and 67 to 69 seconds between consecutive writes, computed from `writes.tsv`.

| Capture | Job | Duration | Set | Manifest | Gitea Pod |
| --- | --- | --- | --- | --- | --- |
| 1 | `gitea-backup-29866957` | 18 s | `20261014T223702Z` | `"capture": "lvm-snapshot"`, `"pause_seconds": 0` | Restarts 0, older than the capture |
| 2 | `gitea-backup-29866972` | 12 s | `20261014T225201Z` | Same | Same |
| 3 | `gitea-backup-29866987` | 11 s | `20261014T230701Z` | Same | Same |
| 4 | `gitea-backup-29867002` | 10 s | `20261014T232201Z` | Same | Same |

Sources: `kubectl get jobs,pods -n gitea` on the control plane after each capture, and `gcloud storage cat gs://<bucket>/primary/<set>/manifest.json`. The manifest shows which capture path ran; it cannot detect a pause, because the snapshot path writes `"pause_seconds": 0` as a constant. The set `20261014T222201Z`, taken by the earlier design, reports `"pause_seconds": 5` and no `capture` field. The evidence that Gitea was not stopped is the Pod age and restart count, and the backup Role, which has no `scale` verb. The Gitea Pod `gitea-58f5548c6c-2v676` was 60 minutes old with 0 restarts after capture 4.

Cleanup of the snapshot:

- Capture 1: on the worker, `sudo lvs k8sdr` listed only `data`, and `findmnt -R /var/lib/k8s-dr-backup` printed nothing.
- Captures 2 to 4: the Pod status reports exit code 0 for the `snapshot`, `dump-postgresql`, and `archive-gitea` containers and for `backup`. Exit 0 shows that `k8sdr-snapshot remove` returned 0: the script runs with `set -e`, so a non-zero `remove` in the `TERM` trap exits non-zero, and a kill after the grace period would show 137. It does not prove the snapshot is gone, because `remove` treats an `lvs` error as no snapshot ([#84](https://github.com/sindredg/k8s-dr/issues/84)). The worker was not inspected after these captures ([#85](https://github.com/sindredg/k8s-dr/issues/85)).

### Uptime check

The [decision 0011 query](../decisions/0011-external-uptime-probe.md#reading-the-results) for 22:30 to 23:23 UTC, read at 23:32 UTC. A read at 23:23 returned two fewer points per region, also with no `false`:

| Region | Points | `false` |
| --- | --- | --- |
| `apac-singapore` | 315 | 0 |
| `eur-belgium` | 318 | 0 |
| `usa-virginia` | 316 | 0 |

The query can fail: for 21:00 to 22:30 UTC, before the DNS record pointed at the primary, it returned 530, 517, and 529 `false` points.

### Restore of a set taken under load

| Step | Command | Result |
| --- | --- | --- |
| Test namespaces | `make restore-test-env` | `failed=0`, 79 s |
| Restore | `make restore RESTORE_NAMESPACE=gitea-restore RESTORE_SET=20261014T232201Z` | `digests match the manifest`, `both archives decrypt and read`, `restored primary/20261014T232201Z in 32 seconds`; `failed=0` |
| Fixtures | `make restore-test-forward`, then `make check-fixtures GIT_URL=http://localhost:3000` | `failed=0` |
| Newest restored commit | Gitea API `repos/recovery-fixture/recovery-fixture/commits` on the restored and the live service | Restored: `f4964f5`, the last write acknowledged before the snapshot (23:21:02 UTC). The next write, `1a2eed5`, acknowledged at 23:22:10, is absent, so it landed after the snapshot. That order is inferred from the restore content; the container log that timestamps the snapshot was not read. The live service had moved on to `2c91b07` |
| New push | `make write-check GIT_URL=http://localhost:3000` | `Push accepted at 2026-10-14T23:26:36Z`; `failed=0` |
| Cleanup | `make restore-test-env-delete` | Exit 0 |

### Dense probe across four more captures

Date: 2026-10-14 23:42 to 2026-10-15 00:38 UTC. Two loads ran together:

- `scripts/capture_probe.py https://git.sindrg.com recovery-fixture/recovery-fixture <log> 0.5`: every 0.5 seconds, one `GET /api/healthz`, which pings the database, and one authenticated `git ls-remote`. A probe counts as failed on an error, a non-200 status, or a time of 2 seconds or more. Against a wrong password it logged `git-exit-128`, and against a name that does not resolve it logged `error-URLError` and `error-TimeoutExpired`. Its unit tests fail when the slow rule is removed.
- `make write-loop WRITE_INTERVAL=0 DRILL_DIR=.drill/m9-probe`: back-to-back pushes, 432 acknowledged, a median of 8 and at most 12 seconds apart, no `write check failed` line.

| Capture | Job run (start to completion) | `healthz` in window | `ls-remote` in window | Writes around it |
| --- | --- | --- | --- | --- |
| `20261014T235202Z` | 23:52:00 to about 23:52:25 (Job deleted by history limit; end reasoned) | 59, 0 failed, max 0.70 s | 60, 0 failed, max 1.89 s | 23:51:57, then 23:52:06 |
| `20261015T000701Z` | 00:07:00 to 00:07:18 | 44, 0 failed, max 0.55 s | 46, 1 slow at 2.11 s | 00:06:57, then 00:07:07 |
| `20261015T002201Z` | 00:22:00 to 00:22:13 | 35, 0 failed, max 0.52 s | 36, 0 failed, max 1.67 s | 00:21:59, then 00:22:08 |
| `20261015T003701Z` | 00:37:00 to 00:37:18 | 44, 0 failed, max 0.79 s | 45, 2 slow at 2.09 and 2.22 s | 00:36:54, then 00:37:02 |

Every manifest reports `"capture": "lvm-snapshot"`, as does `20261014T233701Z`, the capture between the two loads. Each Pod reports exit code 0 for all four containers, and the Gitea Pod `gitea-58f5548c6c-2v676` had 0 restarts at 00:39 UTC. The uptime check for 23:42 to 00:39 UTC returned 342, 342, and 341 points with no `false`.

No probe returned an error or a non-200 status during the whole run. Captures slow requests down:

| Probe | Median inside captures | Median outside | Maximum inside | 99th percentile outside |
| --- | --- | --- | --- | --- |
| `healthz` | 0.288 s | 0.232 s | 0.792 s | 0.521 s |
| `ls-remote` | 1.054 s | 0.783 s | 2.223 s | 1.405 s |

The slowdown fits the copy-on-write cost that [decision 0014](../decisions/0014-snapshot-backups.md#consequences) names, but the cause was not isolated. The run's largest `ls-remote` times, up to 4.79 s, came at 23:42:39 to 23:42:44, outside any capture. Of the 14 `ls-remote` probes at 2 seconds or more, 3 fell inside captures.

### Not verified

- The container logs of captures 1 to 4 were not read, so step 1 of [Check a capture](../runbooks/snapshot-backups.md#check-a-capture) ran only for the Job status. The exit codes stand in for the log lines ([#85](https://github.com/sindredg/k8s-dr/issues/85)).
- The worker was not inspected after captures 2 to 4.
- The load was light: dumps under 400 KB and Gitea archives under 2 MB. The snapshot reserve was not stressed.
- The probe resolution is 0.5 seconds, so a stall shorter than that could pass between probes. The uptime check alone cannot see a stall shorter than a minute.
- The restore used a set from the first run, under the sparse write loop, not one from the dense probe.

### Gate

Passed on 2026-10-15. Nine consecutive scheduled captures, 22:37 to 00:37 UTC, ran on the snapshot path with no Gitea restart. The 23:37 capture ran between the two loads. Across the other eight, the write loops and the uptime check recorded no failure. Across the last four, a probe every 0.5 seconds recorded no error and no non-200 status. Three `ls-remote` requests inside captures took 2 seconds or more, the slowest 2.2 seconds against a usual 0.8. A set taken under the write loop restored with the fixtures intact and the last pre-snapshot write present.
