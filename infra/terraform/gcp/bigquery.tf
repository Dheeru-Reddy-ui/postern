# Warehouse (ADR-003). Postgres is the system of record; these tables receive append-only
# event rows from the outbox shipper. Every column is documented in
# postern/integrations/bigquery/schemas/*.json.

locals {
  # Partition expiration is a cost ceiling (TM-12) and a retention decision.
  # Operational history is the product, so most tables keep three years; llm_calls holds
  # full prompts and responses and keeps 180 days.
  warehouse_tables = {
    runs = {
      description     = "One row per run state transition."
      expiration_days = 1095
      clustering      = ["agent", "user_id"]
    }
    steps = {
      description     = "One row per checkpointed agent step: LLM calls, tool calls, refusals."
      expiration_days = 1095
      clustering      = ["agent", "run_id"]
    }
    llm_calls = {
      description     = "One row per LLM call made through postern/llm/gateway.py (invariant 6)."
      expiration_days = 180
      clustering      = ["agent", "model"]
    }
    findings = {
      description     = "One row per finding state change. Never contains user row data (invariant 4)."
      expiration_days = 1095
      clustering      = ["finding_type", "user_id"]
    }
    ledger = {
      description     = "Value ledger: found, proposed, merged, verified closed (Phase 7)."
      expiration_days = 1095
      clustering      = ["stage", "issue_type"]
    }
    notifications = {
      description     = "Every message decision and interaction from the notification service."
      expiration_days = 1095
      clustering      = ["event_kind", "user_id"]
    }
    eval_results = {
      description     = "One row per eval case trial, from CI and online evals (Phase 4)."
      expiration_days = 1095
      clustering      = ["suite", "agent"]
    }
  }
}

resource "google_bigquery_dataset" "postern" {
  dataset_id                 = "postern"
  friendly_name              = "Postern warehouse"
  description                = "Append-only event tables shipped from Postern's control plane (ADR-003)."
  location                   = var.region
  delete_contents_on_destroy = false

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_table" "event" {
  for_each            = local.warehouse_tables
  dataset_id          = google_bigquery_dataset.postern.dataset_id
  table_id            = each.key
  description         = each.value.description
  deletion_protection = true
  # The schema files live in the package so the worker can encode rows from them too.
  schema     = file("${path.module}/../../../postern/integrations/bigquery/schemas/${each.key}.json")
  clustering = each.value.clustering

  time_partitioning {
    type          = "DAY"
    field         = "event_ts"
    expiration_ms = each.value.expiration_days * 86400000
  }
}

# Delivery is at-least-once, so analysts query these views, which keep one row per event_id.
resource "google_bigquery_table" "dedup" {
  for_each            = local.warehouse_tables
  dataset_id          = google_bigquery_dataset.postern.dataset_id
  table_id            = "${each.key}_dedup"
  description         = "${each.key} with duplicate deliveries removed (one row per event_id)."
  deletion_protection = false

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT * EXCEPT (_postern_row)
      FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY event_ts) AS _postern_row
        FROM `${var.project_id}.${google_bigquery_dataset.postern.dataset_id}.${google_bigquery_table.event[each.key].table_id}`
      )
      WHERE _postern_row = 1
    SQL
  }
}
