# The nightly trigger (ADR-012). pg_cron enqueues every nightly run at 02:17 UTC inside the
# control plane (Phase 2); this starts postern-worker at 02:30 UTC to drain them. Cloud
# Scheduler's free tier covers three jobs per billing account; this is the only one.

resource "google_cloud_scheduler_job" "nightly_worker" {
  name             = "postern-nightly-worker"
  region           = var.region
  description      = "Start postern-worker after the nightly runs are enqueued."
  schedule         = "30 2 * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "60s"

  retry_config {
    retry_count = 2
  }

  # Cloud Run Admin API v2: jobs.run. Documented pattern for scheduled jobs (checked
  # 2026-10-14): https://cloud.google.com/run/docs/execute/jobs-on-schedule
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.worker.name}:run"
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_cloud_run_v2_job_iam_member.scheduler_runs_worker,
  ]
}
