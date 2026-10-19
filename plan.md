# Project plan

Milestones 0 to 10 and 12 are complete, and 11 and 13 to 16 are planned. Milestone 8 rebuilt the environment, which was removed after the drills, from the runbooks. Milestone 9 removed the backup downtime. Milestones 10 to 14 automate and shorten the recovery, measure it unattended, and close the recovery gaps of the lab. Milestone 15 decides how far the lab survives the loss of one zone, and milestone 16 shortens the reading path through the documentation. A step is ticked only after its validation gate passes, and the evidence for each gate is in the [worklogs](docs/worklogs/).

The planned milestones run in the order 12, 11, 13, 14, 15, 16. Unattended drills come first because they test whether the automated recovery works with nobody present, and they give milestone 11 a baseline measured the same way. Milestone numbers stay fixed because decision records and worklogs link to them.

| Milestone | Outcome | Status |
| --- | --- | --- |
| 0. Recovery contract | Scope and measurements defined | Complete |
| 1. Primary infrastructure | Reproducible VMs and network | Complete |
| 2. Kubernetes bootstrap | Repeatable kubeadm cluster | Complete |
| 3. Service deployment | Gitea survives pod restarts | Complete |
| 4. Consistent backups | Offsite data restores successfully | Complete |
| 5. Cold recovery | Independent service in second region | Complete |
| 6. Disaster drill | Measured RTO and RPO | Complete |
| 7. Faster recovery | Optional improvement backed by measurements | Complete |
| 8. Rebuild from the runbooks | The documented procedure builds the environment from nothing | Complete |
| 9. Backups without downtime | Gitea stays up while every backup is captured | Complete |
| 10. Automated recovery | Recovery runs without a person | Complete |
| 11. Faster cluster build | The cluster build no longer dominates recovery time | Planned |
| 12. Unattended drills | Measured recovery with nobody present | Done |
| 13. Failback with data | Optional return that keeps the recovery writes | Planned |
| 14. Close the recovery gaps of the lab | Separate backup project, threat model, and an accurate production readiness page | Planned |
| 15. Zonal resilience decision | A recorded choice on surviving the loss of one zone | Planned |
| 16. Documentation reading path | A short README and one ordered entry point to the documentation | Planned |

## 0. Recovery contract

- [x] Select two regions and document how project costs will be monitored.
- [x] Set a provisional RTO of a few hours and an RPO based on backup frequency.
- [x] Define service recovery: login, known commit and issue present, new push succeeds.
- [x] Define the failure drill, timer start and stop, and data-loss measurement.

**Gate:** The success checks and measurement method are written before provisioning.

## 1. Primary infrastructure

- [x] Provision a primary control-plane VM, worker VM, private network, and restricted administration with Terraform.
- [x] Provision offsite backup storage and remote Terraform state outside the primary region.
- [x] Make recovery credentials available without relying on the primary VMs.

**Gate:** Terraform can reproduce the primary VMs; the nodes can communicate; state, backup storage, and credentials remain accessible independently.

## 2. Kubernetes bootstrap

- [x] Configure Linux, containerd, kubelet, kubeadm, and kubectl with Ansible.
- [x] Initialize the control plane, join the worker, and install a CNI.
- [x] Install the minimum ingress and storage components needed for the service.
- [x] Automate a clean rebuild from fresh VMs.

**Gate:** Nodes are Ready; a disposable app schedules and is reachable; the worker rejoins after a restart; a fresh rebuild follows the same steps.

## Before milestone 3: recovery readiness

These changes are easier before the worker disk holds service data. See the [preparation worklog](docs/worklogs/03-service-deployment.md#preparation).

- [x] Move the backup bucket and project IAM into a shared Terraform root, and the worker data disk out of the regional module.
- [x] Split cluster validation from test-app validation, and record playbook run times.
- [x] Check in CI that every pinned artifact still resolves upstream.
- [x] Decide the milestone 3 service architecture in a decision record.

**Gate:** Both the shared and primary roots plan with no changes after the migration; IAP access still works; cluster-only validation passes; the pin check passes in CI; the milestone 3 decision record is accepted.

## 3. Service deployment

- [x] Bootstrap Flux from the external GitHub repository.
- [x] Deploy Gitea from its Helm chart and PostgreSQL as a StatefulSet, both with persistent volumes. See the [decision 0006 amendment](docs/decisions/0006-service-deployment-architecture.md#amendment-postgresql-and-gitea-implementation).
- [x] Create a user, repository, commit, and issue as recovery fixtures.

**Gate:** Git changes reconcile; Gitea works after its pods restart; all fixture data remains.

**Deferred until needed:** Local `kubectl` access through an IAP tunnel to the API server on port 6443. It needs a firewall rule for the IAP range on 6443 and a local kubeconfig that targets `localhost` with `tls-server-name: kubernetes`. Until then, run `kubectl` on the control plane over IAP SSH.

## 4. Consistent backups

- [x] Capture PostgreSQL and Gitea repository and configuration data at a consistent point.
- [x] Store backups outside the primary region with timestamps and integrity checks.
- [x] Make failed or incomplete backup sets ineligible for restore.
- [x] Harden the backup bucket: a writer that cannot delete or overwrite objects, a retention policy longer than the backup interval plus the drill window with a recorded decision on locking it, an explicit soft-delete policy, and a lifecycle rule that expires noncurrent versions. See [decision 0006](docs/decisions/0006-service-deployment-architecture.md#deferred-to-milestone-4) and [decision 0007](docs/decisions/0007-consistent-backups.md).
- [x] Restore a backup into a separate test environment.

**Gate:** The restored service contains the expected commit and issue and accepts a new push. Record the backup age and restore duration.

## Before milestone 6: cluster metrics

Dashboards for the drill that remain readable after the primary region is lost. See [decision 0009](docs/decisions/0009-cluster-metrics.md) and the [worklog](docs/worklogs/04b-cluster-metrics.md).

- [x] Send node, pod, and Kubernetes object metrics from each cluster to Grafana Cloud through Flux.

**Gate:** The monitoring release is Ready; Grafana Cloud shows both nodes and the service pods for `cluster="primary"`; active series stay under the free-tier limit; node memory requests stay below 80% of allocatable.

## Before milestone 6: service hardening

Close the findings of a security review of the running service. See [decision 0010](docs/decisions/0010-service-hardening.md) and the [worklog](docs/worklogs/04c-service-hardening.md).

- [x] Require sign-in to view Gitea.
- [x] Restrict Traefik egress and deny the metadata server in `cert-manager` and `monitoring`.
- [x] Narrow the node OAuth scopes.
- [x] Check the public surface daily.

**Gate:** Flux is Ready with the policies applied; Traefik serves after a restart; the anonymous API returns 401 or 403; the fixture checks pass; a pod in `monitoring` cannot reach the metadata server; a backup completes with the narrowed scope; the surface check reports no regression.

## 5. Cold recovery

- [x] Provision recovery-region VMs from Terraform with no primary-region dependency.
- [x] Bootstrap Kubernetes with Ansible and kubeadm; reconnect Flux to GitHub.
- [x] Restore Gitea and PostgreSQL from one verified recovery point.
- [x] Route test traffic to the recovered service.

**Gate:** The recovered service passes the same checks while the primary service is isolated. Passed on 2026-10-03; see the [worklog](docs/worklogs/05-cold-recovery.md#validation).

## 6. Disaster drill

- [x] Start an external health probe and record the last successful write.
- [x] Simulate primary-region loss and execute the recovery runbook.
- [x] Verify login, known commit and issue, and a new push through the recovery endpoint.
- [x] Record actual RTO, RPO, manual actions, failures, and cloud cost.
- [x] Fix the runbook and repeat the drill once.

**Gate:** Evidence shows a successful repeatable restore and whether the targets were met. Passed on 2026-10-04: two drills met both targets; see the [worklog](docs/worklogs/06-disaster-drill.md#comparison). Cloud cost is recorded as the time the recovery environment existed, not as a billed amount.

## 7. Faster recovery (optional)

- [x] Identify the largest measured delays and data-loss window from milestone 6.
- [x] Choose one improvement, such as a warm standby or more frequent backups.
- [x] Repeat the same drill and compare recovery time, data loss, and cost.

**Gate:** The improvement has measured benefits and a documented cost trade-off. Passed on 2026-10-04: backups every 15 minutes; see [decision 0012](docs/decisions/0012-backup-every-15-minutes.md) and the [worklog](docs/worklogs/07-faster-recovery.md).

## 8. Rebuild from the runbooks

The environment was removed after milestone 7. Only the Terraform state bucket remained.

- [x] Build the primary from empty state by following the runbooks as written, and record every deviation.
- [x] Fix each deviation in the runbook or the code.
- [x] Recreate the fixtures, confirm a backup set, and restore it into the test namespaces.

**Gate:** The primary serves the fixtures, a backup set restores in the test namespaces, and the worklog lists every step that did not work as documented. Passed on 2026-10-16. The restore evidence is the milestone 9 restore of 2026-10-14; see the [worklog](docs/worklogs/08-rebuild-from-runbooks.md#gate).

## 9. Backups without downtime

Before milestone 9, every backup scaled Gitea to zero for the capture. At one capture every 15 minutes, the service had a short outage four times an hour, and the uptime check recorded it. The backup caused the unavailability the project exists to prevent. See [decision 0014](docs/decisions/0014-snapshot-backups.md).

- [x] Put the worker data filesystem on an LVM logical volume, with free space in the volume group for a snapshot.
- [x] Capture PostgreSQL and the Gitea volume from one LVM snapshot while Gitea keeps running, and remove the backup job's permission to scale Gitea.
- [x] Keep the encrypted set format, so the restore path and the earlier drills stay comparable.
- [x] Prove that no request fails during captures: run a write and probe loop across several scheduled backups.
- [x] Restore a set taken under write load into the test namespaces.

**Gate:** Across at least four scheduled captures under a write loop, the uptime check and the loop record no failure, every manifest reports no pause, and a set taken under load restores with the fixtures intact. Passed on 2026-10-15, with a probe every 0.5 seconds across four of the captures. The manifest field is a constant on the snapshot path, so the Gitea restart count is the evidence of no pause; see the [worklog](docs/worklogs/09-backups-without-downtime.md#gate).

## 10. Automated recovery

See [decision 0013](docs/decisions/0013-automated-recovery.md) and the [worklog](docs/worklogs/10-automated-recovery.md).

- [x] Move the node service accounts and backup grants to the shared root.
- [x] Run every recovery stage from one command that stops at the first failed check and resumes from it.
- [x] Run that command from GitHub Actions with workload identity federation and no stored cloud key.
- [x] Fence the primary and resume recovery backups without a manual edit or a Git change.
- [x] Start the recovery from a scheduled watchdog, with a switch that disables it.
- [x] Automate the return to the primary after a drill.

**Gate:** A recovery started by hand from the workflow completes with no other action, and the watchdog does not start one during a probe failure shorter than its window or while its switch is off. Passed on 2026-10-17: a full recovery from the workflow took 17 min 07 s, the return workflow then restored the primary, and the watchdog started nothing during a 99-second outage with its switch on. The automatic start itself has run against stubs only; [milestone 12](#12-unattended-drills) runs it. See the [worklog](docs/worklogs/10-automated-recovery.md#gate).

## 11. Faster cluster build

Runs after [milestone 12](#12-unattended-drills), so the stage times before and after come from unattended drills.

In attended drill 3 the cluster build took 10 min 42 s of the 16 min 46 s recovery: provisioning 2 min 43 s and the bootstrap playbook 7 min 59 s. The restore took 57 s. Most of the bootstrap installs packages and pulls container images from public sources during the outage. A decision record will compare the options before this is built.

In drill 3 of milestone 12, detection took longer than the rest of the recovery, which is the revisit condition of [decision 0015](docs/decisions/0015-unattended-drills.md#detection-stays-with-the-github-schedule-for-now). This milestone also decides how the watchdog starts.

- [ ] Build a node image with the pinned packages and the container images of every deployed workload, from the existing Ansible roles, and store it outside Finland.
- [ ] Bootstrap both clusters from that image, and keep the playbook able to build from the plain Ubuntu image.
- [ ] Measure the provisioning and bootstrap stages before and after, from the stage timeline.
- [ ] Decide in a decision record whether the watchdog leaves the GitHub schedule, from the detection times of the milestone 12 drills.

**Gate:** The bootstrap stage of a recovery takes at most half of its mean of 8 min 00 s in drills 2 and 3 of milestone 12, and a recovery succeeds with no public registry or package mirror reachable from the nodes.

## 12. Unattended drills

Runs before milestone 11. Drills 1 to 3 on 2026-10-04 were attended; this milestone measures the recovery with the watchdog's automatic start. [Decision 0013](docs/decisions/0013-automated-recovery.md#schedule) estimated detection at up to about 25 minutes because GitHub starts scheduled runs late. On 2026-10-19 the scheduled watchdog runs came up to 42 min 57 s apart, and detection took 13 min 15 s and 18 min 15 s in the two drills that passed. [Decision 0015](docs/decisions/0015-unattended-drills.md) defines how a drill isolates the primary, how it ends, and where its results are kept, and [decision 0016](docs/decisions/0016-drills-on-demand.md) lets a drill start on demand and end on the primary whatever the recovery does. Progress is in the [worklog](docs/worklogs/12-unattended-drills.md), and the results are on the [results page](docs/drills.md).

- [x] Load a dataset of 2 GiB, so the restore time is measured at a realistic size.
- [x] Start a drill from the schedule or on demand: isolate the primary, then take no action until the watchdog has started the recovery and the recovery has passed its checks. Record the recovery time from the first failed probe, including detection.
- [x] End each drill on the primary with no person acting: with the return workflow after a recovery, or by starting the primary VMs again.
- [x] Keep the results of every unattended drill in one table that the README links to.

**Gate:** Two consecutive drills, started by the schedule or by a dispatch, meet both targets with no person acting after the start, the watchdog started each recovery, and the worklog compares them with drills 1 to 3. Passed on 2026-10-19: two drills started by a dispatch recovered in 34 min 21 s and 36 min 00 s, including detection, and lost 10 min 18 s and 5 min 09 s of writes. See the [worklog](docs/worklogs/12-unattended-drills.md#gate) and its [independent review](docs/worklogs/12-unattended-drills.md#independent-review).

## 13. Failback with data (optional)

- [ ] Return to the primary region from a backup of the recovery cluster, so writes accepted during the outage survive.
- [ ] Measure the downtime and data loss of the return.

**Gate:** A write made on the recovery cluster is present on the primary after the return.

## 14. Close the recovery gaps of the lab

[Production readiness](docs/production-readiness.md) lists where this lab differs from production. This milestone closes the gaps that weaken recovery, and leaves the rest documented as lab scope.

- [ ] Keep the backups in a separate project, with a locked retention policy and a second copy in another location.
- [ ] Write a threat model for the backup, recovery, and pipeline paths.
- [ ] Check every shell script with ShellCheck in CI.
- [ ] Update each production readiness row that milestones 9 to 14 changed, and state plainly what remains lab scope.

**Gate:** A recovery restores from the separate backup project, deleting a set there fails for the project owner, and the production readiness page matches what is built.

## 15. Zonal resilience decision

Both nodes and the worker data disk are in one zone. The loss of that zone is handled like the loss of the region: a full recovery in Belgium. Surviving a zone loss inside the region is a separate problem, and building for it raises cost and the number of nodes every recovery builds.

- [ ] Write a decision record that compares a stable control-plane endpoint, workers in two zones, a regional persistent disk, three control-plane nodes, and replicated PostgreSQL, with their cost and their effect on recovery time.
- [ ] Build what the record accepts, and list the rest on the production readiness page as lab scope.

**Gate:** The decision record is accepted. If anything is built, the service survives a stop of every VM in the zone that holds the worker, and a regional recovery still meets both targets.

## 16. Documentation reading path

The documentation keeps every decision, measurement, and failure, which makes it long for a first reader.

- [ ] Shorten the README to the overview, the results, and links.
- [ ] Add one page that orders the documentation for a first reading, and separates current state (architecture, runbooks, decisions) from history (worklogs, troubleshooting).
- [ ] Replace repeated facts with links to one copy.

**Gate:** From the README, a reader reaches the results, the architecture, and the recovery procedure in at most two links, and the documentation checks pass.
