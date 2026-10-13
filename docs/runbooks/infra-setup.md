# Runbook: set up Postern's infrastructure from nothing

Run every command from the repo root unless a step says `cd`. Commands are written for
PowerShell. Every `terraform apply` happens only after Dheeru has read the plan.

**Prerequisites**

- A Google Cloud account with an active billing account.
- Terraform 1.16.x (`winget install Hashicorp.Terraform`).
- Google Cloud SDK (`winget install Google.CloudSDK`).
- Node 22, for `npx supabase@2.120.0`.
- `gh`, signed in to the account that owns `Dheeru-Reddy-ui/postern`.

## 1. Sign in to Google Cloud (Dheeru, in your own terminal)

Both commands open a browser.

```bash
gcloud auth login
```
```bash
gcloud auth application-default login
```

Find the billing account ID and its currency:

```bash
gcloud billing accounts list
```

If the currency isn't INR, set `budget_currency` and `budget_amount` in step 4 to match.
The budget must use the billing account's currency.

## 2. Bootstrap: project, billing link, state bucket

1. Pick a project ID, for example `postern-prod-` plus four random characters.
2. Fill in the variables:
   ```bash
   cd infra/terraform/gcp/bootstrap
   Copy-Item terraform.tfvars.example terraform.tfvars   # then fill in project_id and billing_account
   terraform init
   terraform plan -out bootstrap.tfplan
   ```
3. **Review the plan.** Then apply it and point gcloud at the new project:
   ```bash
   terraform apply bootstrap.tfplan
   gcloud config set project <project_id>
   gcloud auth application-default set-quota-project <project_id>
   ```

## 3. Move every state into the bucket

1. Create three backend files in `infra/terraform/`. They aren't secret, so commit them.
   ```
   backend-bootstrap.hcl:   bucket = "<project_id>-tfstate"   prefix = "bootstrap"
   backend-gcp.hcl:         bucket = "<project_id>-tfstate"   prefix = "gcp"
   backend-supabase.hcl:    bucket = "<project_id>-tfstate"   prefix = "supabase"
   ```
2. Migrate the bootstrap's state into the bucket:
   ```bash
   terraform init -migrate-state -backend-config=../../backend-bootstrap.hcl
   ```
   `gcp/bootstrap/backend.tf` is committed with its `backend "gcs"` block enabled, so on a
   brand-new setup you must comment that block out for the very first `init` and `apply`,
   then restore it for this step. Postern's real project was migrated on 2026-10-13.
3. Delete the local `terraform.tfstate*` files only after `terraform plan` shows no
   changes.

> **Lesson from the first run (2026-10-13):** a brand-new project can't be its own quota
> project until `serviceusage` is enabled. So the bootstrap enables its APIs through the
> default provider, and every resource in `gcp/` that needs an API declares
> `depends_on = [google_project_service.enabled]`.

## 4. Apply the main GCP root

```bash
cd infra/terraform/gcp
Copy-Item terraform.tfvars.example terraform.tfvars   # then fill in project_id, billing_account
terraform init -backend-config=../backend-gcp.hcl
terraform plan -out gcp.tfplan
```

**Review the plan,** then:

```bash
terraform apply gcp.tfplan
```

## 5. Point CI at the infrastructure

None of these values is secret. Setting them switches on the deploy job in `ci.yml`.

```bash
gh variable set GCP_PROJECT_ID --body "<project_id>"
gh variable set GCP_WIF_PROVIDER --body "$(terraform -chdir=infra/terraform/gcp output -raw wif_provider)"
gh variable set GCP_DEPLOYER_SA --body "$(terraform -chdir=infra/terraform/gcp output -raw deployer_service_account)"
```

The next push to `main` deploys. See [deploy-and-rollback.md](deploy-and-rollback.md).

## 6. Supabase

Follow [supabase-setup.md](supabase-setup.md).

## 7. Verify

- **No service-account keys exist** (TM-29). Each command must print nothing:
  ```bash
  foreach ($sa in "postern-api","postern-worker","postern-deployer") { gcloud iam service-accounts keys list --iam-account "$sa@<project_id>.iam.gserviceaccount.com" --managed-by=user }
  ```
- **No drift.** Exit code 0 means no changes. Run this in each root:
  ```bash
  terraform plan -detailed-exitcode
  ```
- **No JSON key anywhere in the repo or CI:** `uv run pytest tests/credential_audit` passes.

## 8. Test the budget alert (Phase 1 "Done when")

A budget can't send a test email; it only emails when real cost crosses a threshold. Both
Postern budgets count cost **before credits**, including free-tier and free-trial credits,
so normal Cloud Run usage registers even when the net bill is ₹0.

1. Set `test_budget_enabled = true` in `infra/terraform/gcp/terraform.tfvars`. Plan, review
   and apply. This adds a ₹1 budget that alerts at 1%.
2. Generate a little usage, for example by running the worker a few times:
   `gcloud run jobs execute postern-worker --region us-central1 --wait`.
3. Wait for the email to the billing account's admins. Cost data can lag by up to a day. If
   nothing arrives within 48 hours, check Billing → Reports for this project's cost before
   credits.
4. Set `test_budget_enabled = false`. Plan, review and apply to remove the test budget.

**Faster evidence, without waiting for the email.** Both budgets also publish their status
to the Pub/Sub topic `postern-budget-alerts`. Google publishes on its own schedule, often
within the hour. Read the messages without acknowledging them:

```bash
gcloud pubsub subscriptions pull postern-budget-alerts-inspect --project <project_id> --limit 10 --format=json
```

Each message's base64 `data` decodes to JSON with `budgetDisplayName`, `costAmount`,
`budgetAmount`, `currencyCode` and, once a threshold is crossed, `alertThresholdExceeded`.
