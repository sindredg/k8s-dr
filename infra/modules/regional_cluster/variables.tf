variable "project_id" {
  description = "Project that owns the regional cluster."
  type        = string
}

variable "name_prefix" {
  description = "Unique prefix for this regional cluster."
  type        = string
}

variable "region" {
  description = "Cluster region."
  type        = string
}

variable "zone" {
  description = "Zone in the cluster region."
  type        = string

  validation {
    condition     = startswith(var.zone, "${var.region}-")
    error_message = "The zone must belong to the cluster region."
  }
}

variable "subnet_cidr" {
  description = "Non-overlapping RFC 1918 address range for this regional cluster."
  type        = string
}

variable "machine_type" {
  description = "Machine type for both initial nodes."
  type        = string
}

variable "boot_disk_size_gb" {
  description = "Boot disk size for each node."
  type        = number
}

variable "worker_data_disk_size_gb" {
  description = "Dedicated worker persistent disk size."
  type        = number
}

variable "boot_image" {
  description = "Ubuntu image family or pinned image self-link."
  type        = string
}

variable "labels" {
  description = "Common resource labels."
  type        = map(string)
  default     = {}
}
