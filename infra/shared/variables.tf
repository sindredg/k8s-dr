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

variable "backup_clusters" {
  description = "Cluster name to the IAM member of its worker node service account. Each may create objects under <cluster>/ and read the whole bucket."
  type        = map(string)

  validation {
    condition     = alltrue([for name in keys(var.backup_clusters) : can(regex("^[a-z][a-z0-9-]*$", name))])
    error_message = "Cluster names must be lowercase letters, digits, and hyphens."
  }

  validation {
    condition     = alltrue([for member in values(var.backup_clusters) : startswith(member, "serviceAccount:")])
    error_message = "Backup writers must be service accounts."
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
