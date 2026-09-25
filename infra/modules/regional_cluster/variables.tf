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

variable "worker_data_disk_id" {
  description = "ID of the dedicated worker data disk in the cluster zone, owned by the calling root."
  type        = string
}

variable "boot_image" {
  description = "Pinned Ubuntu 24.04 LTS image self-link. Images are global, so every region uses the same tested image."
  type        = string
  default     = "https://www.googleapis.com/compute/v1/projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20260918"
  nullable    = false
}

variable "labels" {
  description = "Common resource labels."
  type        = map(string)
  default     = {}
}
