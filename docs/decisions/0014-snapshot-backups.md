# 0014: Capture backups from an LVM snapshot

Status: Proposed on 2026-10-14. [Milestone 9](../../plan.md#9-backups-without-downtime) builds and validates it. It replaces the [scale-to-zero capture](0007-consistent-backups.md#pause-gitea-by-scaling-it-to-zero) of decision 0007; the rest of 0007 stands.

Date: 2026-10-14

## Problem

Every backup scales Gitea to zero, dumps PostgreSQL, archives the Gitea volume, and scales Gitea back up. With one capture every 15 minutes ([decision 0012](0012-backup-every-15-minutes.md)), the service is unavailable four times an hour. The manifests recorded pauses of 4 to 5 seconds, and the uptime check recorded failed points from two of its three regions at a capture. A push or a page load in that window fails. The backup creates the outage that the project exists to prevent.

The pause exists because the database and the repositories must come from the same moment. A live dump and a live archive taken seconds apart can disagree: a push between them leaves a repository ahead of or behind its database rows.

## Decision

Take both from one point-in-time snapshot of the filesystem that holds them, while Gitea keeps running.

PostgreSQL and the Gitea volume are both Local Path Provisioner directories on the worker data disk. That disk becomes an LVM physical volume. The data filesystem is a logical volume that leaves 20% of the volume group free. A backup:

1. Creates a classic LVM snapshot of the data volume in the free space. Device-mapper freezes the filesystem while it switches the snapshot in, so pending writes are flushed and new writes wait for milliseconds. Nothing stops.
2. Mounts the snapshot and bind-mounts the PostgreSQL and Gitea volume directories to fixed paths.
3. Starts a temporary PostgreSQL server on the snapshot's data directory, with no network listener. It recovers from its WAL as after a power loss, then `pg_dump` reads it. PostgreSQL documents this as a valid backup when the data directory and WAL are on one atomic snapshot.
4. Archives the Gitea volume from the same snapshot.
5. Encrypts both with age, uploads them with the manifest as before, and removes the snapshot.

The set format does not change, so `restore.sh`, the restore test, and the recovery runbook stay as they are. The manifest keeps `pause_seconds` at `0` and adds `capture: "lvm-snapshot"`.

### Where each step runs

| Step | Runs as | Why |
| --- | --- | --- |
| Create, mount, and remove the snapshot | `/usr/local/sbin/k8sdr-snapshot` on the worker, installed by Ansible, called through `nsenter` from a privileged native sidecar in the backup Pod. The sidecar enters the worker's mount and IPC namespaces through the worker's `/proc`, mounted read-only. | LVM and mounts need root on the host. LVM waits for udev through System V semaphores, so it runs in the host IPC namespace. The host `/proc` mount replaces `hostPID`, so the other containers keep their own PID namespace. The sidecar starts before the capture steps and receives `SIGTERM` after them, so removal runs on every exit path that is not `SIGKILL`. The next run removes a snapshot left by `SIGKILL` before it creates one. |
| Dump PostgreSQL | An init container as UID 999 | The data directory belongs to the PostgreSQL user, and the server refuses to run as root. |
| Archive the Gitea volume | An init container as UID 1000 | The same user Gitea runs as. |
| Upload, manifest, and heartbeat | The main container as UID 1000 | Unchanged from decision 0007. |

The backup Job no longer scales Gitea, so its Role loses the `deployments/scale` permission. It also no longer connects to PostgreSQL over the network. The restore Job still needs both, so it gets its own service account, Role, and network policies, `gitea-restore`, instead of sharing the backup's.

### Data disk layout

This replaces steps 2 and 3 of the worker disk rules in [decision 0005](0005-kubernetes-bootstrap-architecture.md#preserve-the-worker-disk-and-fail-safely). The storage role makes a blank data disk the only physical volume of the volume group `k8sdr`, creates the logical volume `data` on 80% of it, and formats that as ext4. It reuses an existing `k8sdr` volume group and fails on any other signature, including the plain ext4 disk of the earlier layout. Converting that disk in place is not possible, so it is rebuilt; the [snapshot backups runbook](../runbooks/snapshot-backups.md) gives the steps.

## Alternatives

| Option | Benefit | Cost |
| --- | --- | --- |
| LVM snapshot on the worker (selected) | No downtime. The database and repositories come from one instant. Set format, encryption, retention lock, and prefix fencing stay. | A privileged sidecar in the backup Pod. The data disk layout changes, so an existing ext4 data disk must be rebuilt. |
| Compute Engine disk snapshots | No downtime, no host code, and a fast disk-level restore. | The sets are no longer age-encrypted to keys outside the cluster, have no retention lock, and have no per-cluster write prefix. The restore path, the restore test, and the drill comparisons change. |
| Live dump and archive, repaired with `gitea doctor` on restore | No downtime and no privilege. | Already rejected in decision 0007: the two captures are seconds apart, and every restore needs a repair whose result varies. |
| Hide the pause with Traefik retries | Small change. | The outage remains. A retry cannot cover a Pod that is gone for several seconds, and a push can still fail. |
| A CSI driver with volume snapshots, such as TopoLVM | Kubernetes-native snapshots. | PostgreSQL and Gitea are two volumes. Two snapshots are two instants unless a volume group snapshot is used, which the driver does not support. Replaces the storage layer. |

## Consequences

- The capture is crash-consistent at one instant, not quiesced. A restore equals the state after a power loss at that instant. PostgreSQL recovers from that by design. Git writes objects before it updates a ref, and the freeze flushes the page cache, so a repository is never half-written. One window remains: a push whose refs are updated but whose post-receive hook has not yet updated the database. That window lasts milliseconds, against seconds for a live dump. The restore check covers the fixtures, not that window.
- The backup Pod contains a privileged container that enters the host mount and IPC namespaces. Anyone who can create Pods in the `gitea` namespace could already read the Gitea data; with this change they can also gain root on the worker. Flux is the only writer of that namespace.
- The snapshot is classic copy-on-write. If more than the free space (20% of the disk) is written during one capture, the snapshot becomes invalid, that backup fails and alerts, and the data volume is unaffected. A thin pool would avoid the reserve, but an overfilled thin pool can stop writes to the data volume itself.
- Writes to the data volume are slower while a snapshot exists, because each first write to a block is also copied. A capture lasts seconds to minutes.
- The recovery cluster uses the same storage role, so its backups after a failover have no pause either.
