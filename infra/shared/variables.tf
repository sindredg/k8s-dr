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

variable "backup_operator_member" {
  description = "IAM member allowed to manage backup objects. Keep independent of the state operator."
  type        = string
}

variable "recovery_reader_member" {
  description = "IAM member able to read backups during recovery without primary VM access."
  type        = string
}

variable "admin_member" {
  description = "IAM user or group allowed to administer nodes in every region through IAP and OS Login."
  type        = string
}
