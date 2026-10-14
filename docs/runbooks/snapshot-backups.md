# Snapshot backups: operator procedure

Status: Written on 2026-10-15, not yet run. [Milestone 9](../../plan.md#9-backups-without-downtime) validates it, and its [worklog](../worklogs/09-backups-without-downtime.md) records the results.

Convert the worker data disk to the LVM layout of [decision 0014](../decisions/0014-snapshot-backups.md), check that captures run without pausing Gitea, and clear a snapshot that a killed run left behind.

## Inputs and access

- The generated inventory exists for the target cluster. See [Kubernetes bootstrap](kubernetes-bootstrap.md).
- IAP SSH access to both VMs, and the variables from that runbook:

  ```bash
  export PROJECT_ID=<project-id>
  export PRIMARY_ZONE=<zone>
  export CONTROL_PLANE_NAME=<control-plane-vm>
  export WORKER_NAME=<worker-vm>
  ```

- For a disk that holds service data: a complete backup set newer than the last write you want to keep, and the backup age private key. See [backup and restore](backup-restore.md).

Run `kubectl` on the control plane:

```bash
kc() { gcloud compute ssh "$CONTROL_PLANE_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command="sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl $*"; }
```

## Convert a data disk from the earlier layout

Only needed once per data disk, when `make bootstrap` stops with `Refusing unexpected filesystem 'ext4'`. The steps erase the data disk. The service data comes back from the backup set in step 7.

1. Confirm the disk layout.

   ```bash
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap \
     --command='sudo blkid -o value -s TYPE "$(readlink -f /dev/disk/by-id/google-worker-data)"; findmnt -n -o SOURCE,FSTYPE /var/lib/k8s-dr'
   ```

   Expected: `ext4`, and the data disk device mounted at `/var/lib/k8s-dr`. If the first line is `LVM2_member`, the disk is already converted; stop here.

2. Note the newest complete set, if the disk holds data to keep. The restore in step 7 uses it.

   ```bash
   BUCKET="$(terraform -chdir=infra/shared output -raw backup_bucket_name)"
   gcloud storage ls "gs://${BUCKET}/primary/*/manifest.json" | tail -n 1
   ```

   Expected: a set from the last 15 minutes, such as `gs://<bucket>/primary/20261015T080701Z/manifest.json`. Writes after it are lost.

3. Move every Pod off the worker. The service is down from here until step 6.

   ```bash
   kc drain "$WORKER_NAME" --ignore-daemonsets --delete-emptydir-data --timeout=300s
   ```

   Expected: `node/<worker-vm> drained`. The `gitea` and `postgresql` Pods are `Pending`, because no other node can run them.

4. Unmount and erase the data disk, and remove its line from `/etc/fstab`.

   ```bash
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='
     set -e
     sudo umount /var/lib/k8s-dr
     sudo wipefs --all "$(readlink -f /dev/disk/by-id/google-worker-data)"
     sudo sed -i "\| /var/lib/k8s-dr |d" /etc/fstab
     sudo blkid -o value -s TYPE "$(readlink -f /dev/disk/by-id/google-worker-data)" || echo blank'
   ```

   Expected: `wipefs` lists the erased ext4 signature, and the last line is `blank`. If `umount` reports `target is busy`, a Pod still uses the disk: rerun step 3 and check `kc get pods -A -o wide`.

5. Build the new layout.

   ```bash
   make bootstrap
   ```

   Expected: `failed=0`. The worker recap reports changes from `Create the volume group only on a blank worker data device`, `Create the data logical volume`, `Create ext4 only on a blank data logical volume`, and `Install the backup snapshot command`.

6. Return the worker to service.

   ```bash
   kc uncordon "$WORKER_NAME"
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap \
     --command='findmnt -n -o SOURCE,FSTYPE /var/lib/k8s-dr; sudo vgs k8sdr; sudo lvs k8sdr'
   make validate-services
   ```

   Expected: `/dev/mapper/k8sdr-data ext4`; `vgs` shows about 20% of the volume group free; `lvs` lists only `data`. PostgreSQL and Gitea start on empty volumes, and `make validate-services` ends with `failed=0`.

7. Restore the set from step 2, if the disk held data.

   ```bash
   make restore RESTORE_NAMESPACE=gitea RESTORE_SET=<set from step 2>
   make check-fixtures
   ```

   Expected: both end with `failed=0`.

## Check a capture

1. Read the newest backup Job and the state of its containers.

   ```bash
   kc get jobs -n gitea --sort-by=.metadata.creationTimestamp
   kc logs -n gitea job/<newest job> --all-containers --prefix
   ```

   Expected: the newest `gitea-backup-<number>` Job is `Complete`. The log shows `snapshot created`, `temporary server started on the snapshot`, `dumped PostgreSQL`, `archived the Gitea volume`, `uploaded primary/<set>`, and `snapshot removed`.

2. Read the manifest of that set.

   ```bash
   gcloud storage cat "gs://${BUCKET}/primary/<set>/manifest.json" | jq '{pause_seconds, capture}'
   ```

   Expected: `"pause_seconds": 0` and `"capture": "lvm-snapshot"`.

3. Confirm the snapshot is gone and the Gitea Pod did not restart.

   ```bash
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='sudo lvs k8sdr; findmnt -R /var/lib/k8s-dr-backup'
   kc get pods -n gitea -l app.kubernetes.io/name=gitea
   ```

   Expected: `lvs` lists only `data`, `findmnt` prints nothing, and the Gitea Pod's age is older than the capture with `0` restarts.

## Validate captures under write load

1. Start the write loop in a separate terminal. It pushes a commit every 60 seconds and logs each one.

   ```bash
   make write-loop WRITE_INTERVAL=60
   ```

   Expected: a line per write in `.drill/writes.tsv`, and no `write check failed` line.

2. Let at least four scheduled captures run (one hour). Then check each one as in [Check a capture](#check-a-capture), and read the uptime check for that hour in the Cloud Monitoring console.

   Expected: four `Complete` Jobs with `pause_seconds` `0`, no `write check failed` line, and no failed uptime check in any region.

3. Restore the newest set into the test namespaces, as in [backup and restore](backup-restore.md).

   Expected: the restore and the fixture checks end with `failed=0`, and the newest restored commit is a write from the loop.

## Remove a leftover snapshot

A backup Pod killed with `SIGKILL` can leave the snapshot in place. The next scheduled run removes it before it creates a new one. To remove it earlier:

```bash
gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command='sudo /usr/local/sbin/k8sdr-snapshot remove; sudo lvs k8sdr'
```

Expected: `removed k8sdr/backup`, then `lvs` lists only `data`. While a snapshot exists, each first write to a block of the data volume is copied, so writes are slower.
