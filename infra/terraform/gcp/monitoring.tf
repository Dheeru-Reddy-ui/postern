# Operator alerts (Phase 3). Every Postern alert is a structured log line with a
# `postern_alert` field (postern/core/redaction.py); one log-match policy emails all of them.
#
# Cost: log-match alerting is free until 2027-09-01, then $0.35 a month for this one policy
# (Cloud Observability pricing, checked 2026-10-14). Flagged in the Phase 3 report.

resource "google_monitoring_notification_channel" "operator_email" {
  display_name = "Postern operator email"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.enabled]
}

resource "google_monitoring_alert_policy" "postern_alert" {
  display_name = "Postern alert"
  combiner     = "OR"
  severity     = "CRITICAL"

  conditions {
    display_name = "A Postern component raised an alert, or the worker failed"
    condition_matched_log {
      filter = join(" OR ", [
        "(resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${google_cloud_run_v2_job.worker.name}\" AND (jsonPayload.postern_alert:* OR severity>=ERROR))",
        "(resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${google_cloud_run_v2_service.api.name}\" AND jsonPayload.postern_alert:*)",
        "(resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"${google_cloud_scheduler_job.nightly_worker.name}\" AND severity>=ERROR)",
      ])
      label_extractors = {
        alert = "EXTRACT(jsonPayload.postern_alert)"
      }
    }
  }

  # Required for log-match conditions: at most one email per alert kind every 5 minutes.
  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.operator_email.id]

  documentation {
    mime_type = "text/markdown"
    content   = "A Postern component logged an alert. The `alert` label says which one; the runbook for each is in docs/runbooks/runtime.md in the Postern repo."
  }
}
