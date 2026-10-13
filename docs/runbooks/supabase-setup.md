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
  Paused projects don't count. On 2026-10-13 both slots were in use (ClinicalContext plus
  one other project), so ClinicalContext was paused to make room for `postern-control`.
  The Phase 3 testbed needs the remaining slot, so the other active free project must be
  paused or removed before Phase 3.

## 2. Access token (Dheeru, in your own terminal)

1. Dashboard → Account → Access Tokens → create a **scoped** token named
   `postern-terraform`, limited to the `postern` organization, with a short expiry (for
   example 30 days). Leave every permission at **No access** except:

   | Group | Permission | Level |
   |---|---|---|
   | Account and organization | Organizations | Read |
   | Account and organization | Organization Projects | Read-write |
   | Project | Project Settings | Read |
   | Database | SSL Enforcement | Read-write |
   | Database | Connection Pooling | Read |

   Do not grant API Keys or API Key Secrets. The migration step below uses `--db-url`, so
   it never needs `supabase link`. Scoped tokens can't be edited later; if Terraform
   reports `missing_permissions`, create a new token with them.
2. Set it for the current terminal only. Never commit it, never put it in CI, and never paste
   it into a chat:
   ```bash
   $env:SUPABASE_ACCESS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR((Read-Host -AsSecureString "Supabase token")))
   ```
   This works in Windows PowerShell 5.1 and in PowerShell 7. The token is typed hidden and
   lives only in that terminal session.
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
$pw = terraform -chdir=infra/terraform/supabase output -raw database_password
$url = (terraform -chdir=infra/terraform/supabase output -json pooler_urls | ConvertFrom-Json).transaction -replace '\[YOUR-PASSWORD\]', $pw -replace ':6543/', ':5432/'
npx supabase@2.120.0 db push --db-url $url --dry-run
npx supabase@2.120.0 db push --db-url $url
Remove-Variable pw, url
```

- **Session mode, not transaction mode.** Supabase returns only the transaction-mode pooler
  URL (port 6543). The same host on port 5432 is session mode, which migrations need.
- **No API-key permissions needed.** `--db-url` connects straight to the database, so the
  operator token never needs them.
- **The password stays inside the command.** It comes from Terraform state, and nothing
  prints it.

Verified against `postern-control` (ref `bpymxbzldxhsqrvrybyo`) on 2026-10-13.

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
