# Values CI needs. None are secret; they go into GitHub repository variables
# (docs/runbooks/infra-setup.md, step 6).

output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.postern.repository_id}/postern"
}

output "wif_provider" {
  description = "GitHub variable GCP_WIF_PROVIDER."
  value       = google_iam_workload_identity_pool_provider.postern_repo.name
}

output "deployer_service_account" {
  description = "GitHub variable GCP_DEPLOYER_SA."
  value       = google_service_account.deployer.email
}

output "worker_job" {
  value = google_cloud_run_v2_job.worker.name
}
