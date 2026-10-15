# Terraform owns the shape of each service and job: identity, scaling, limits and env.
# CI owns which image runs (ADR-010), so image and client fields are ignored here.
#
# Secrets reach the containers as environment variables read from Secret Manager at
# startup. Each component references only the secrets secret_access.json lets it read
# (TM-05); tests/credential_audit/test_gcp_iam.py checks the two agree.

locals {
  # env var => secret, per component
  api_secret_env = {
    POSTERN_DATABASE_URL           = "control-plane-db-url-api"
    POSTERN_SUPABASE_CLIENT_SECRET = "supabase-oauth-client-secret"
    POSTERN_TOKEN_KEY              = "token-encryption-key"
  }
  worker_secret_env = {
    POSTERN_DATABASE_URL = "control-plane-db-url-worker"
  }
}

resource "google_cloud_run_v2_service" "api" {
  name                = "postern-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL" # GitHub webhooks and OAuth callbacks are public
  deletion_protection = true

  template {
    service_account                  = google_service_account.api.email
    max_instance_request_concurrency = 40
    timeout                          = "30s"

    # Cost ceiling (TM-12): scale to zero, never past two instances.
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.placeholder_service_image

      env {
        name  = "POSTERN_COMPONENT"
        value = "api"
      }
      env {
        name  = "POSTERN_API_URL"
        value = var.api_url
      }
      env {
        name  = "POSTERN_SUPABASE_CLIENT_ID"
        value = var.supabase_oauth_client_id
      }
      env {
        name  = "POSTERN_WORKER_SA_EMAIL"
        value = google_service_account.worker.email
      }
      dynamic "env" {
        for_each = local.api_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.placeholder[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = false
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
      traffic, # rollbacks pin traffic; see docs/runbooks/deploy-and-rollback.md
    ]
  }

  # A revision can't start until each secret it references can be read.
  depends_on = [google_project_service.enabled, google_secret_manager_secret_iam_member.reader]
}

# Anyone may call the API: webhooks and OAuth callbacks come from the internet, and each
# endpoint authenticates its caller itself (signatures, OAuth state, sessions).
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_job" "worker" {
  name                = "postern-worker"
  location            = var.region
  deletion_protection = true

  template {
    # Cost ceiling (TM-12): one task at a time.
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.worker.email
      timeout         = "600s"
      max_retries     = 1

      containers {
        image = var.placeholder_job_image

        env {
          name  = "POSTERN_COMPONENT"
          value = "worker"
        }
        env {
          name  = "POSTERN_API_URL"
          value = var.api_url
        }
        env {
          name  = "POSTERN_GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "POSTERN_BQ_DATASET"
          value = google_bigquery_dataset.postern.dataset_id
        }
        dynamic "env" {
          for_each = local.worker_secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.placeholder[env.value].secret_id
                version = "latest"
              }
            }
          }
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  # A revision can't start until each secret it references can be read.
  depends_on = [google_project_service.enabled, google_secret_manager_secret_iam_member.reader]
}
