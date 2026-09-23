# 0004: Remove the Terraform-managed budget alert

Status: Accepted for milestone 1; operator validation pending.

Date: 2026-09-23

## Context

The project-scoped monthly budget repeatedly blocked the primary Terraform apply. The operator chose to remove the alert rather than continue troubleshooting it. Removing its value from local `terraform.tfvars` was insufficient because Terraform still declared the input as required.

## Decision

Remove the billing budget resource, its project-number lookup, and its three input variables from the primary Terraform root. Do not enable the Billing Budgets API as part of the operator procedure. Review project charges and remaining credits manually in Cloud Billing. This supersedes the spend-alert decisions in [decision 0002](0002-recovery-contract.md) and [decision 0003](0003-regional-infrastructure-and-state.md); it does not change the milestone 1 infrastructure gate.

## Trade-off

The primary configuration no longer makes a budget-specific API request or needs a billing-account input. There is no automated threshold warning from this Terraform configuration. Manual review does not limit spending.
