variable "project_id" {
  description = "Existing Google Cloud project that owns the state bucket."
  type        = string
}

variable "state_bucket_name" {
  description = "Globally unique name for the Terraform state bucket."
  type        = string
}

variable "state_bucket_location" {
  description = "State location outside the primary region."
  type        = string
  default     = "europe-west1"

  validation {
    condition     = var.state_bucket_location != "europe-north1"
    error_message = "State must be stored outside the Finland primary region."
  }
}

variable "state_operator_member" {
  description = "IAM member (for example, group:name@example.com) authorized to read and write state objects."
  type        = string
}
