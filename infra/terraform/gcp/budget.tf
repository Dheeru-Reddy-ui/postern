# ₹100/month budget (CLAUDE.md). Budgets only notify; they never stop spending (TM-12).
# Alert emails go to the billing account's administrators and users (Google's default
# recipients), which is the account owner. Both budgets also publish their current cost to
# Pub/Sub, so budget status can be read by machines (Phase 7's Platform Auditor) without
# waiting for an email.

resource "google_pubsub_topic" "budget_alerts" {
  name       = "postern-budget-alerts"
  depends_on = [google_project_service.enabled]
}

# Cloud Billing publishes budget notifications as this Google-managed account.
resource "google_pubsub_topic_iam_member" "billing_publishes_budget_alerts" {
  topic  = google_pubsub_topic.budget_alerts.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:billing-budget-alert@system.gserviceaccount.com"
}

# Pull subscription for reading budget status by hand (and later by the Platform Auditor).
# Unacknowledged messages are kept for one day; the subscription never expires.
resource "google_pubsub_subscription" "budget_alerts_inspect" {
  name                       = "postern-budget-alerts-inspect"
  topic                      = google_pubsub_topic.budget_alerts.id
  ack_deadline_seconds       = 20
  message_retention_duration = "86400s"

  expiration_policy {
    ttl = ""
  }
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "postern monthly"

  budget_filter {
    projects        = ["projects/${data.google_project.this.number}"]
    calendar_period = "MONTH"
    # Count gross cost, so free-trial credits can't hide real usage.
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    pubsub_topic                   = google_pubsub_topic.budget_alerts.id
    schema_version                 = "1.0"
    disable_default_iam_recipients = false
  }

  depends_on = [google_project_service.enabled]
}

# Phase 1 "Done when": receive a real budget alert. A ₹1 budget with a 1% threshold fires
# on the first non-zero cost. Enabled only for the test, then removed (infra-setup runbook).
resource "google_billing_budget" "alert_test" {
  count           = var.test_budget_enabled ? 1 : 0
  billing_account = var.billing_account
  display_name    = "postern alert test (temporary)"

  budget_filter {
    projects               = ["projects/${data.google_project.this.number}"]
    calendar_period        = "MONTH"
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = "1"
    }
  }

  threshold_rules {
    threshold_percent = 0.01
  }

  all_updates_rule {
    pubsub_topic                   = google_pubsub_topic.budget_alerts.id
    schema_version                 = "1.0"
    disable_default_iam_recipients = false
  }

  depends_on = [google_project_service.enabled]
}
