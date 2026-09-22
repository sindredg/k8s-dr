variable "project_id" {
  description = "Existing project for the cluster, backup bucket, and budget filter."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account linked to the project."
  type        = string
}

variable "name_prefix" {
  description = "Short, unique prefix for primary resources (at most 23 characters)."
  type        = string

  validation {
    condition     = length(var.name_prefix) <= 23 && can(regex("^[a-z][a-z0-9-]*[a-z0-9]$", var.name_prefix))
    error_message = "Use 2-23 lowercase letters, digits, and hyphens; start with a letter and end with a letter or digit."
  }
}

variable "primary_region" {
  description = "Primary region selected by the recovery contract."
  type        = string
  default     = "europe-north1"

  validation {
    condition     = var.primary_region == "europe-north1"
    error_message = "Milestone 1 provisions only the Finland primary region."
  }
}

variable "primary_zone" {
  description = "Finland zone for both initial nodes."
  type        = string
}

variable "primary_subnet_cidr" {
  description = "Unused private CIDR for the Finland VPC subnet."
  type        = string
}

variable "machine_type" {
  description = "Machine type for each node; default gives 2 vCPU and 4 GiB RAM."
  type        = string
  default     = "e2-custom-2-4096"
}

variable "boot_disk_size_gb" {
  description = "Balanced persistent boot disk size per node."
  type        = number
  default     = 30
}

variable "worker_data_disk_size_gb" {
  description = "Balanced persistent data disk size on the worker."
  type        = number
  default     = 50
}

variable "boot_image" {
  description = "Ubuntu LTS image family; pin an image for exact rebuilds."
  type        = string
  default     = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
}

variable "backup_bucket_name" {
  description = "Globally unique offsite application backup bucket name."
  type        = string
}

variable "backup_bucket_location" {
  description = "Backup bucket location outside Finland."
  type        = string
  default     = "europe-west1"

  validation {
    condition     = var.backup_bucket_location != "europe-north1"
    error_message = "Backups must be stored outside the Finland primary region."
  }
}

variable "backup_operator_member" {
  description = "IAM member allowed to manage backup objects. Keep independent of the state operator."
  type        = string
}

variable "recovery_reader_member" {
  description = "IAM member able to read backups during recovery without primary VM access."
  type        = string
}

variable "admin_member" {
  description = "IAM user or group allowed to administer nodes through IAP and OS Login."
  type        = string
}

variable "budget_currency_code" {
  description = "ISO 4217 currency code of the linked billing account."
  type        = string

  validation {
    condition     = can(regex("^[A-Z]{3}$", var.budget_currency_code))
    error_message = "Use the three-letter billing account currency code."
  }
}

variable "budget_amount" {
  description = "Monthly alert amount in the billing account currency, equivalent to the intended 1,000 NOK at operator-selected exchange rate."
  type        = number

  validation {
    condition     = var.budget_amount > 0
    error_message = "Budget amount must be positive."
  }
}
