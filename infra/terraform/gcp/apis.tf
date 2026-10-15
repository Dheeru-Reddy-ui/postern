# Only the APIs Postern uses. New projects also come with Google's default set enabled;
# this list is what Postern depends on, and nothing outside it should be added casually.
locals {
  enabled_apis = toset([
    "aiplatform.googleapis.com",           # Gemini on Vertex AI, keyless (Phase 3, ADR-012)
    "artifactregistry.googleapis.com",     # container images
    "bigquery.googleapis.com",             # warehouse
    "bigquerystorage.googleapis.com",      # Storage Write API (ADR-003)
    "billingbudgets.googleapis.com",       # ₹100 budget
    "cloudresourcemanager.googleapis.com", # project IAM
    "cloudscheduler.googleapis.com",       # starts postern-worker nightly (ADR-012)
    "iam.googleapis.com",                  # service accounts, WIF pools
    "iamcredentials.googleapis.com",       # WIF service account impersonation
    "logging.googleapis.com",              # structured logs, log-based alerts
    "monitoring.googleapis.com",           # the operator alert policy (monitoring.tf)
    "pubsub.googleapis.com",               # budget notifications
    "run.googleapis.com",                  # postern-api, postern-worker
    "secretmanager.googleapis.com",        # secret placeholders
    "serviceusage.googleapis.com",
    "sts.googleapis.com", # WIF token exchange
  ])
}

resource "google_project_service" "enabled" {
  for_each           = local.enabled_apis
  service            = each.value
  disable_on_destroy = false
}
