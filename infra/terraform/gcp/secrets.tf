# Secret Manager placeholders: secrets exist, values don't. A secret with no versions costs
# nothing; each active version beyond the free six costs about $0.06/month.

locals {
  secret_access = jsondecode(file("${path.module}/secret_access.json")).secrets

  runtime_members = {
    api    = google_service_account.api.member
    worker = google_service_account.worker.member
  }

  secret_readers = merge([
    for secret, spec in local.secret_access : {
      for reader in spec.readers : "${secret}/${reader}" => {
        secret = secret
        member = local.runtime_members[reader]
      }
    }
  ]...)
}

resource "google_secret_manager_secret" "placeholder" {
  for_each  = local.secret_access
  secret_id = each.key
  annotations = {
    purpose = each.value.purpose
  }

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# Secret-level bindings only. No runtime account gets a project-level Secret Manager role.
resource "google_secret_manager_secret_iam_member" "reader" {
  for_each  = local.secret_readers
  secret_id = google_secret_manager_secret.placeholder[each.value.secret].id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}
