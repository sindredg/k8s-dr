# Billing budget apply errors

Status: Fixes written; operator reports the retry worked. Full validation evidence is pending.

## Symptom

In `infra/primary`, `terraform apply` first failed with `Call to unknown function` for `round()` at `main.tf:101`. The next attempt failed because `all_updates_rule` specified neither `monitoring_notification_channels` nor `pubsub_topic`.

## Confirmed cause

Terraform does not have a `round()` function. The selected Google provider also requires a Monitoring channel or Pub/Sub topic when `all_updates_rule` is present. Its `enable_project_level_recipients` setting alone did not satisfy that requirement.

## Fix

The budget now uses Terraform's supported `floor()` function to calculate whole nanos. The optional `all_updates_rule` was removed. Default threshold emails go to Billing Account Administrators and Billing Account Users, so the operator must confirm an intended recipient has one of those roles.

## Verification

The operator reported that the retry worked, but did not provide the command output. No successful plan, apply, budget recipient check, or milestone gate result is recorded. Recheck with `terraform validate`, a reviewed `terraform plan`, and the budget page in Cloud Billing. Do not share state or raw plan files.
