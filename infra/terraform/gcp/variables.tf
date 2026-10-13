variable "project_id" {
  description = "Postern's GCP project (created by gcp/bootstrap)."
  type        = string
}

variable "billing_account" {
  description = "Billing account ID the budget watches."
  type        = string
}

variable "region" {
  description = "Region for every regional resource (CLAUDE.md fixes us-central1)."
  type        = string
  default     = "us-central1"
}

variable "budget_currency" {
  description = "Must match the billing account's currency."
  type        = string
  default     = "INR"
}

variable "budget_amount" {
  description = "Monthly budget in whole units of budget_currency (CLAUDE.md: ₹100)."
  type        = number
  default     = 100
}

variable "test_budget_enabled" {
  description = "Temporarily adds a tiny budget so a real alert email can be verified."
  type        = bool
  default     = false
}

# Public facts about the GitHub repo, used to pin Workload Identity Federation to it.
# IDs, not names, so a renamed or re-created repo can't inherit deploy rights.
variable "github_repository" {
  type    = string
  default = "Dheeru-Reddy-ui/postern"
}

variable "github_repository_id" {
  type    = string
  default = "1416674974"
}

variable "github_owner_id" {
  type    = string
  default = "172974763"
}

# Placeholder images used only until CI deploys the first real image. Terraform ignores
# image changes afterwards (docs/adr/ADR-010-infrastructure-and-deploy-ownership.md).
variable "placeholder_service_image" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello@sha256:e2a01cf1e784fcc57c558ed2b1d1f76456de90771c0ad99ae3b6f57722c7dbf3"
}

variable "placeholder_job_image" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/job@sha256:831ba01b2d55931e33e49088fbe806c2beb0f736247c1484b76a151740bb9150"
}
