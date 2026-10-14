# Backups without downtime

Status: In progress. Built, with local checks passed; not yet run on a cluster. This file is the record of [milestone 9](../../plan.md#9-backups-without-downtime).

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

The set format did not change, so `restore.sh` did not change.

## Local checks

All results below are local, from 2026-10-14 and 2026-10-15. None ran on a cluster.

| Check | Where | Result | Can it fail |
| --- | --- | --- | --- |
| `make check` | Local | 208 tests `OK`; yamllint, ansible-lint, ShellCheck, and syntax checks pass | Ten edits that break the design each failed the matching test: a privileged capture container, a wrong claim, a wrong `PGDATA` major, a missing `HostToContainer`, a renamed volume group, no snapshot reserve, `vgcreate` on a non-blank disk, a write verb in the backup Role, PostgreSQL egress for the backup Pod, and a renamed script step |
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

In progress.
