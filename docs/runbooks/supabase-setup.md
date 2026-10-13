# Runbook: Postern's own Supabase project

This is **Postern's control plane** (`postern-control`), never a user's project. Run every
command from the repo root unless a step says `cd`.

## What manages what

| Setting | Managed by |
|---|---|
| Project (name, region, org) | Terraform: `supabase_project.control` |
| SSL enforcement | Terraform: `supabase_settings.control` |
| `pgmq` and `pg_cron` extensions | Supabase CLI migration, because the Terraform provider (v1.11) has no extension resource |
| Tables, roles, RLS, queues, schedules (Phase 2) | Declarative files in `supabase/schemas/`, plus migrations |

## 1. Organization and free-plan slots

- Create a Supabase organization named `postern` on the Free plan (dashboard → New
  organization). Note its slug. ADR-008 keeps it separate from the testbed organization.
- The Free plan allows two free projects across every organization you own or administer.
  Paused projects don't count. On 2026-10-13 one was active (ClinicalContext), so
  `postern-control` takes the second slot. The Phase 3 testbed needs another one, so pause
  ClinicalContext's project before Phase 3.

## 2. Access token (Dheeru, in your own terminal)

1. Dashboard → Account → Access Tokens → create `postern-terraform`.
2. Set it for the current terminal only. Never commit it, never put it in CI, and never paste
   it into a chat:
   ```bash
   $env:SUPABASE_ACCESS_TOKEN = Read-Host -MaskInput "Supabase token"
   ```
   The Terraform provider and the Supabase CLI both read `SUPABASE_ACCESS_TOKEN`.

## 3. Create the project with Terraform

```bash
cd infra/terraform/supabase
Copy-Item terraform.tfvars.example terraform.tfvars   # then set organization_slug
terraform init -backend-config=../backend-supabase.hcl
terraform plan -out supabase.tfplan
```

**Review the plan,** then:

```bash
terraform apply supabase.tfplan
terraform output project_ref
```

## 4. Enable pgmq and pg_cron (exact CLI steps)

From the repo root, in the same terminal as step 2:

```bash
$env:SUPABASE_DB_PASSWORD = terraform -chdir=infra/terraform/supabase output -raw database_password
npx supabase@2.120.0 link --project-ref <project_ref>
npx supabase@2.120.0 db push --dry-run
npx supabase@2.120.0 db push
Remove-Item Env:SUPABASE_DB_PASSWORD
```

The dry run must list exactly one migration,
`20261013000000_enable_pgmq_and_pg_cron.sql`.

## 5. Verify

Run this in the dashboard's SQL editor for `postern-control`:

```sql
select extname, extversion from pg_extension where extname in ('pgmq', 'pg_cron') order by 1;
```

You should see two rows. Also confirm that **Queues → Settings → "Expose Queues via
PostgREST"** is **off**, which is the default. Postern's queues are reachable only from
Postgres clients.

## Rolling back the extensions

`drop extension pg_cron;` permanently deletes every cron job, and `drop extension pgmq;`
deletes every queue and its messages. Only do this on a fresh project, and write a migration
for it. Never run it by hand on the live control plane.
