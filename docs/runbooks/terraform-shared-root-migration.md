# Terraform shared-root migration

Status: Completed on 2026-09-25; see the [preparation worklog](../worklogs/03-service-deployment.md#preparation). The one-time `migrations.tf` files were removed afterward, so this procedure cannot be rerun from the current tree. It remains as a record and as a pattern for moving resources between roots. A fresh deployment follows the [primary infrastructure procedure](primary-infrastructure.md).

This procedure moves the backup bucket, its IAM members, and the project-level administrator grants from `infra/primary` to the new `infra/shared` root. It also moves the worker data disk from the regional module to the primary root. No cloud resource is created, replaced, or destroyed. The only change is the backup bucket's `environment` label, which moves from `primary` to `shared`. See the [amendment to decision 0003](../decisions/0003-regional-infrastructure-and-state.md#amendment-shared-root-and-root-owned-data-disk) for the reasons.

Run every command from the repository root on the operator machine, with the refactor branch checked out. Treat plans and state as sensitive, as in the [primary procedure](primary-infrastructure.md#inputs-and-access).

## Order and safety

Apply `infra/shared` first, then `infra/primary`. Between the two applies both roots manage the same resources, which is harmless while neither root is destroyed. Do not run `terraform destroy` in any root during this procedure.

The state bucket keeps earlier object generations, so the previous primary state stays recoverable.

## Procedure

1. Set shell variables from the current primary outputs, and record the current primary state generation. Run this before step 5 renames the zone output.

   ```bash
   cd infra/primary
   terraform init -backend-config=backend.hcl
   export PROJECT_ID="$(terraform output -raw project_id)"
   export PRIMARY_ZONE="$(terraform output -raw primary_zone)"
   export WORKER_NAME="$(terraform output -raw worker_name)"
   export OS_LOGIN_USER="$(gcloud compute os-login describe-profile --format='value(posixAccounts[0].username)')"
   gcloud storage ls --all-versions "gs://REPLACE_WITH_STATE_BUCKET/primary/"
   cd ../..
   ```

   Expected: `terraform init` succeeds and the four variables are nonempty. The listing shows `default.tfstate` with a generation number, for example `default.tfstate#1727000000000000`. Note the newest generation locally as the rollback point. Do not publish it with the bucket name.

2. Create the shared root's local files. Copy the values for `project_id`, `backup_bucket_name`, `backup_bucket_location`, `admin_member`, `backup_operator_member`, and `recovery_reader_member` from `infra/primary/terraform.tfvars`. Use the same state bucket as the primary backend.

   ```bash
   cd infra/shared
   umask 077
   cp terraform.tfvars.example terraform.tfvars
   cp backend.hcl.example backend.hcl
   chmod 600 terraform.tfvars backend.hcl
   # Edit both files and replace every placeholder.
   terraform init -backend-config=backend.hcl
   ```

   Expected: Terraform initializes the GCS backend with prefix `shared` and installs the same provider version as the primary root.

3. Plan and apply the shared root.

   ```bash
   terraform plan -out=shared-migration.tfplan
   terraform apply shared-migration.tfplan
   rm shared-migration.tfplan
   ```

   Expected plan: `Plan: 6 to import, 0 to add, 1 to change, 0 to destroy.` The six imports are the backup bucket, its two IAM members, and three `google_project_iam_member.admin` grants. The one change is on `google_storage_bucket.backups` and touches only `labels`, `effective_labels`, and `terraform_labels` (`environment` becomes `shared`). If the plan adds, destroys, or replaces anything, or changes another bucket attribute, do not apply. Report the plan summary and the resource lines.

4. Remove the moved values from the primary root's local variables. Delete the `backup_bucket_name`, `backup_bucket_location`, `backup_operator_member`, and `recovery_reader_member` lines from `infra/primary/terraform.tfvars`. Also delete the `boot_image` line: the module now tracks the same image as its default. Keep `admin_member`.

   ```bash
   cd ../primary
   grep -c -E '^(backup_|recovery_reader_member|boot_image)' terraform.tfvars
   ```

   Expected: `0`.

5. Plan and apply the primary root.

   ```bash
   terraform plan -out=primary-migration.tfplan
   terraform apply primary-migration.tfplan
   rm primary-migration.tfplan
   ```

   Expected plan: `Plan: 0 to add, 0 to change, 0 to destroy.` It shows that `module.primary_cluster.google_compute_disk.worker_data` has moved to `google_compute_disk.worker_data`. Six resources "will no longer be managed by Terraform, but will not be destroyed": the backup bucket, its two IAM members, and the three project IAM members. Output changes add `zone` and `subnet_cidr` and remove `primary_zone`, `primary_subnet_cidr`, and `backup_bucket_name`. If the plan destroys or replaces anything, do not apply.

6. Confirm both roots converge and access still works.

   ```bash
   terraform plan -detailed-exitcode
   terraform output zone
   cd ../shared
   terraform plan -detailed-exitcode
   terraform output backup_bucket_name
   gcloud projects get-iam-policy "$PROJECT_ID" --flatten=bindings --filter="bindings.members:REPLACE_WITH_ADMIN_MEMBER" --format='value(bindings.role)'
   cd ../..
   gcloud compute ssh "$WORKER_NAME" --project="$PROJECT_ID" --zone="$PRIMARY_ZONE" --tunnel-through-iap --command=true
   ```

   Expected: both plans exit `0`. `zone` prints the Finland zone. The IAM listing includes `roles/iap.tunnelResourceAccessor`, `roles/compute.osAdminLogin`, and `roles/compute.instanceAdmin.v1`. IAP SSH exits `0`.

7. Regenerate the Ansible inventory with the renamed outputs.

   ```bash
   python3 scripts/prepare_ansible_inventory.py --terraform-dir infra/primary --ssh-user "$OS_LOGIN_USER" --ssh-key "$HOME/.ssh/google_compute_engine" --output ansible/inventory/generated/hosts.json
   ```

   Expected: the script exits `0`. It fails with `missing Terraform outputs: zone, subnet_cidr` if step 5 was not applied.

8. Remove the one-time migration blocks. Delete `infra/shared/migrations.tf` and `infra/primary/migrations.tf` in a follow-up commit, then plan both roots again.

   Expected: both plans exit `0`. The import blocks must go before anyone deploys from scratch, because an import block fails when its resource does not exist.

## Rollback

If the primary apply produces an unexpected result, stop. Do not run further applies. Restore the primary state generation recorded in step 1 with `gcloud storage cp` from that generation to the same object only after reviewing what changed, then check out `main` and plan. The shared root's imports do not change cloud resources, so `terraform state rm` in `infra/shared` reverses them without affecting the bucket or grants.

Send the plan summary lines, exit codes, and relevant resource lines with project IDs, bucket names, and members replaced. Record results in the worklog only after they are observed.
