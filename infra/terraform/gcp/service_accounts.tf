# One service account per component, each with only what it needs (TM-05, TM-29).
# No service account keys are ever created; CI uses Workload Identity Federation (wif.tf).

resource "google_service_account" "api" {
  account_id   = "postern-api"
  display_name = "postern-api (Cloud Run service)"
  description  = "Webhooks, OAuth callbacks, MCP. Reads only the secrets in secret_access.json."

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "worker" {
  account_id   = "postern-worker"
  display_name = "postern-worker (Cloud Run Job, tier 0)"
  description  = "Tier 0 agents. Never reads the Supabase OAuth client secret or GitHub App key."

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "deployer" {
  account_id   = "postern-deployer"
  display_name = "postern-deployer (GitHub Actions via WIF)"
  description  = "Pushes images and rolls out new revisions. Reads no secrets."

  depends_on = [google_project_service.enabled]
}

# --- worker ---------------------------------------------------------------------------

# Appends warehouse rows through the Storage Write API (ADR-003). Dataset-scoped.
resource "google_bigquery_dataset_iam_member" "worker_writes_warehouse" {
  dataset_id = google_bigquery_dataset.postern.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.worker.member
}

# --- deployer -------------------------------------------------------------------------

resource "google_artifact_registry_repository_iam_member" "deployer_pushes_images" {
  location   = google_artifact_registry_repository.postern.location
  repository = google_artifact_registry_repository.postern.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.deployer.member
}

# Project-level because rolling out a revision also polls project-scoped operations.
resource "google_project_iam_member" "deployer_deploys_cloud_run" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = google_service_account.deployer.member
}

# Deploying a revision that runs as a service account requires actAs on that account.
resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  for_each = {
    api    = google_service_account.api.name
    worker = google_service_account.worker.name
  }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}
