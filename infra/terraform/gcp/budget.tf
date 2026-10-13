# ₹100/month budget (CLAUDE.md). Budgets only notify; they never stop spending (TM-12).
# With no all_updates_rule, alert emails go to the billing account's administrators and
# users (Google's default recipients), which is the account owner.

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

  depends_on = [google_project_service.enabled]
}
