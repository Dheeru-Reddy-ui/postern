variable "organization_slug" {
  description = "Slug of the Supabase organization that owns postern-control (ADR-008: org `postern`)."
  type        = string
}

variable "region" {
  description = "Supabase region; us-east-1 is the closest common region to Cloud Run in us-central1."
  type        = string
  default     = "us-east-1"
}
