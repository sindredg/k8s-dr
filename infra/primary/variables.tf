variable "project_id" {
  description = "Existing project for the primary cluster."
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
  description = "Optional image self-link override. Null uses the tested image pinned in the regional cluster module."
  type        = string
  default     = null
}

variable "admin_member" {
  description = "IAM user or group allowed to use the node service accounts. Must match admin_member in infra/shared."
  type        = string
}
