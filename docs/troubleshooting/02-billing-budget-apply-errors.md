# Billing budget apply errors

Status: Historical incident. The operator chose to remove the budget from Terraform; the revised configuration is not yet validated.

## Symptom

In `infra/primary`, `terraform apply` first failed with `Call to unknown function` for `round()` at `main.tf:101`. The next attempt failed because `all_updates_rule` specified neither `monitoring_notification_channels` nor `pubsub_topic`.

A later apply created the reported infrastructure resources but failed to create the budget with HTTP 403: local Application Default Credentials (ADC) had no quota project. The error identified a different API consumer project than the intended project.

The operator later reported that Terraform still prompted for a number after the budget amount was removed from local `terraform.tfvars`. No new command output was supplied for that attempt.

## Confirmed cause

Terraform does not have a `round()` function. The selected Google provider also requires a Monitoring channel or Pub/Sub topic when `all_updates_rule` is present. Its `enable_project_level_recipients` setting alone did not satisfy that requirement.

The later budget failure was caused by missing quota-project configuration on local ADC. Enabling the Budget API on the intended project did not make ADC use that project for quota.

The number prompt was caused by the still-required `budget_amount` variable declaration in `infra/primary/variables.tf`. Removing its local value did not remove the input requirement.

## Fix

The earlier attempts replaced `round()` with `floor()` and removed `all_updates_rule`. These changes did not resolve the later API error.

At the operator's request, the current configuration removes the budget resource, its required inputs, and the project-number lookup entirely. The [primary operator procedure](../runbooks/primary-infrastructure.md) no longer enables the Billing Budgets API or requires an ADC quota-project step for budget creation. Remove stale budget keys from local `terraform.tfvars` to avoid undeclared-variable warnings.

## Verification

No successful budget creation, no-change plan, or milestone gate result is recorded. The operator should run a fresh plan with the revised configuration. Expect no infrastructure changes if the earlier resource creations are in state; if a budget was created later, expect its deletion. Stop and report any proposed VM, disk, network, or bucket replacement before applying. Do not share state or raw plan files.
