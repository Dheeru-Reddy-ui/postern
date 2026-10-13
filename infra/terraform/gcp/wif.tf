# GitHub Actions deploys by exchanging its OIDC token for short-lived Google credentials.
# No JSON key exists anywhere (CLAUDE.md, TM-29).

locals {
  deploy_workflow_ref = "${var.github_repository}/.github/workflows/ci.yml@refs/heads/main"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  description               = "OIDC tokens from GitHub Actions for ${var.github_repository}."

  depends_on = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "postern_repo" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "postern-repo"
  display_name                       = "postern repo, main branch"

  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.ref"                 = "assertion.ref"
    "attribute.event_name"          = "assertion.event_name"
    "attribute.job_workflow_ref"    = "assertion.job_workflow_ref"
  }

  # Only pushes to main, running this repo's CI workflow, from this repo (by numeric ID).
  attribute_condition = join(" && ", [
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.repository_owner_id == '${var.github_owner_id}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.event_name == 'push'",
    "assertion.job_workflow_ref == '${local.deploy_workflow_ref}'",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_via_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}
