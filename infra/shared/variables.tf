variable "project_id" {
  description = "Existing project for the backup bucket and project IAM."
  type        = string
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

variable "clusters" {
  description = "Cluster name to the name_prefix of its regional root. Each cluster gets node service accounts; its worker may create objects under <cluster>/ and read the whole bucket."
  type        = map(string)
  default = {
    primary  = "k8sdr-primary"
    recovery = "k8sdr-recovery"
  }

  validation {
    condition     = alltrue([for name in keys(var.clusters) : can(regex("^[a-z][a-z0-9-]*$", name))])
    error_message = "Cluster names must be lowercase letters, digits, and hyphens."
  }

  # Service account IDs allow 30 characters, and "-worker" takes 7.
  validation {
    condition     = alltrue([for prefix in values(var.clusters) : length(prefix) <= 23 && can(regex("^[a-z][a-z0-9-]*[a-z0-9]$", prefix))])
    error_message = "Each name_prefix must match the validation of the regional roots."
  }
}

variable "recovery_reader_member" {
  description = "IAM member able to read backups during recovery without primary VM access."
  type        = string
}

variable "admin_member" {
  description = "IAM user or group allowed to administer nodes in every region through IAP and OS Login."
  type        = string
}

variable "probe_host" {
  description = "Public name the uptime probe requests. It stays the same across a cutover."
  type        = string
  default     = "git.sindrg.com"
}

variable "state_bucket_name" {
  description = "Terraform state bucket from infra/bootstrap. The recovery pipeline reads every state and writes under its own prefixes."
  type        = string
}

variable "github_repository_id" {
  description = "Numeric ID of the GitHub repository whose workflow may sign in as the pipeline. The ID survives a rename of the repository."
  type        = string
  default     = "1382368836"
}

variable "github_environment" {
  description = "GitHub environment the recovery workflow runs in. Only main can deploy to it."
  type        = string
  default     = "recovery"
}

variable "secret_locations" {
  description = "Regions that hold the age key secrets, outside Finland."
  type        = list(string)
  default     = ["europe-west1", "europe-west4"]

  validation {
    condition     = length(var.secret_locations) >= 2 && !contains(var.secret_locations, "europe-north1")
    error_message = "Keep the age keys in at least two regions outside the Finland primary region."
  }
}
