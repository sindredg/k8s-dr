# Project plan

Update each milestone's `Status` to `Pending`, `In progress`, or `Complete`. Mark its steps complete only after the validation gate passes. Record evidence in the repo as each milestone finishes.

| Milestone | Outcome | Status |
| --- | --- | --- |
| 0. Recovery contract | Scope and measurements defined | Complete |
| 1. Primary infrastructure | Reproducible VMs and network | Complete |
| 2. Kubernetes bootstrap | Repeatable kubeadm cluster | Complete |
| 3. Service deployment | Gitea survives pod restarts | Pending |
| 4. Consistent backups | Offsite data restores successfully | Pending |
| 5. Cold recovery | Independent service in second region | Pending |
| 6. Disaster drill | Measured RTO and RPO | Pending |
| 7. Faster recovery | Optional improvement backed by measurements | Pending |

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

- [ ] Move the backup bucket and project IAM into a shared Terraform root, and the worker data disk out of the regional module.
- [ ] Split cluster validation from test-app validation, and record playbook run times.
- [ ] Check in CI that every pinned artifact still resolves upstream.
- [ ] Decide the milestone 3 service architecture in a decision record.

**Gate:** Both the shared and primary roots plan with no changes after the migration; IAP access still works; cluster-only validation passes; the pin check passes in CI; the milestone 3 decision record is accepted.

## 3. Service deployment

- [ ] Bootstrap Flux from the external GitHub repository.
- [ ] Deploy Gitea and PostgreSQL with Helm and persistent volumes.
- [ ] Create a user, repository, commit, and issue as recovery fixtures.

**Gate:** Git changes reconcile; Gitea works after its pods restart; all fixture data remains.

## 4. Consistent backups

- [ ] Capture PostgreSQL and Gitea repository and configuration data at a consistent point.
- [ ] Store backups outside the primary region with timestamps and integrity checks.
- [ ] Make failed or incomplete backup sets ineligible for restore.
- [ ] Restore a backup into a separate test environment.

**Gate:** The restored service contains the expected commit and issue and accepts a new push. Record the backup age and restore duration.

## 5. Cold recovery

- [ ] Provision recovery-region VMs from Terraform with no primary-region dependency.
- [ ] Bootstrap Kubernetes with Ansible and kubeadm; reconnect Flux to GitHub.
- [ ] Restore Gitea and PostgreSQL from one verified recovery point.
- [ ] Route test traffic to the recovered service.

**Gate:** The recovered service passes the same checks while the primary service is isolated.

## 6. Disaster drill

- [ ] Start an external health probe and record the last successful write.
- [ ] Simulate primary-region loss and execute the recovery runbook.
- [ ] Verify login, known commit and issue, and a new push through the recovery endpoint.
- [ ] Record actual RTO, RPO, manual actions, failures, and cloud cost.
- [ ] Fix the runbook and repeat the drill once.

**Gate:** Evidence shows a successful repeatable restore and whether the targets were met.

## 7. Faster recovery (optional)

- [ ] Identify the largest measured delays and data-loss window from milestone 6.
- [ ] Choose one improvement, such as a warm standby or more frequent backups.
- [ ] Repeat the same drill and compare recovery time, data loss, and cost.

**Gate:** The improvement has measured benefits and a documented cost trade-off.
