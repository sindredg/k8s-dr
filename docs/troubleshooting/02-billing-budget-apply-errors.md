# Billing budget apply errors

Status: Two configuration fixes written; a later budget creation attempt failed on local ADC quota configuration. Verification is pending.

## Symptom

In `infra/primary`, `terraform apply` first failed with `Call to unknown function` for `round()` at `main.tf:101`. The next attempt failed because `all_updates_rule` specified neither `monitoring_notification_channels` nor `pubsub_topic`.

A later apply created the reported infrastructure resources but failed to create the budget with HTTP 403: local Application Default Credentials (ADC) had no quota project. The error identified a different API consumer project than the intended project.

## Confirmed cause

Terraform does not have a `round()` function. The selected Google provider also requires a Monitoring channel or Pub/Sub topic when `all_updates_rule` is present. Its `enable_project_level_recipients` setting alone did not satisfy that requirement.

The later budget failure was caused by missing quota-project configuration on local ADC. Enabling the Budget API on the intended project did not make ADC use that project for quota.

## Fix

The budget now uses Terraform's supported `floor()` function to calculate whole nanos. The optional `all_updates_rule` was removed. Default threshold emails go to Billing Account Administrators and Billing Account Users, so the operator must confirm an intended recipient has one of those roles.

The operator must run `gcloud auth application-default set-quota-project "$PROJECT_ID"` with the intended project ID, then review a new Terraform plan before applying. This requires `serviceusage.services.use` on that project. The [primary operator procedure](../runbooks/primary-infrastructure.md) now includes this step before Terraform initialization.

## Verification

No successful budget creation, no-change plan, budget recipient check, or milestone gate result is recorded. After setting the ADC quota project, run `terraform plan -out=primary.tfplan` and inspect it. If the other resources completed, expect only budget creation and no replacement actions. Apply the saved plan, then confirm a no-change plan and the budget in Cloud Billing. Do not share state or raw plan files.
