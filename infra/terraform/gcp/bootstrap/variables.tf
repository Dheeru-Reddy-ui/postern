variable "project_id" {
  description = "Globally unique ID for Postern's GCP project, e.g. postern-prod-1a2b."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Project IDs are 6-30 chars: lowercase letters, digits, hyphens."
  }
}

variable "billing_account" {
  description = "Billing account ID (XXXXXX-XXXXXX-XXXXXX) linked to the project."
  type        = string
}
