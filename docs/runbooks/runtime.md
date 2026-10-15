# Runbook: the runtime (worker, connections, alerts)

Phase 3 ([ADR-012](../adr/ADR-012-runtime.md)). Run commands from the repo root.

## Setup

Set these first (PowerShell):

```bash
$P = "postern-prod-8ybj"; $R = "us-central1"
$G = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
```

Operator database commands read `POSTERN_DATABASE_URL`: the session-pooler URL for
`postgres`, built as in `supabase-setup.md` step 4. It's never printed.

## The nightly cycle

| UTC | What | Where |
|---|---|---|
| 02:17 | pg_cron enqueues one run per active connection, for each nightly agent | `postern-nightly-runs` in `postern-control` |
| 02:30 | Cloud Scheduler starts `postern-worker` | `postern-nightly-worker` |
| 02:30 + | The worker alerts on stale connections, drains `tier0`, then ships warehouse events to BigQuery | Cloud Run Job logs |

**Run the worker now** (for example, after enqueuing a run by hand):

```bash
& $G run jobs execute postern-worker --project $P --region $R --wait
```

**Enqueue one Sentinel run by hand** (SQL editor, as `postgres`; the key makes it idempotent):

```sql
select postern.enqueue_run('security_sentinel', 'human', 'manual:2026-10-15:<connection_id>', '<connection_id>');
```

**See what a run did, from the audit log alone:**

```bash
uv run postern replay <run_id>
```

## Alerts

Every alert is a log line with a `postern_alert` field. One Cloud Monitoring policy, "Postern
alert", emails the operator, at most once every 5 minutes per alert kind. The `alert` label
in the email says which kind it is.

| `alert` | Meaning | What to do |
|---|---|---|
| `missed_nightly_run` | An active connection hasn't been checked in 26 hours | Check the worker's last execution and the scheduler. If the run failed, the `run_failed` alert says why. |
| `run_failed` | A run ended as failed, so a check didn't happen | `postern replay <run_id>`. The error code says which step failed. `incomplete_check` means Postern's own catalog checks couldn't run; check the read-only query endpoint. |
| `policy_refusal` | An agent tried a tool its grant doesn't cover | Treat as a possible prompt injection or bug. Replay the run, and open an incident if it isn't explained. |
| `connection_unavailable:<reason>` | The broker refused a token: `connection_revoked`, `refresh_failed`, `token_rejected` or `temporary_access_enabled` | Send the owner a new connect link. If temporary access is on, they must turn it off first (ADR-005). |
| `llm_spend_cap_reached` | This month's LLM spend reached the cap, so runs are parked `awaiting_human` (`llm_spend_cap`) | Findings are already recorded; only explanations wait. Raise the cap only through a reviewed change to `postern/runtime.yaml`, or wait for the next month. |
| `llm_unavailable` | Both models failed for a call | Findings are recorded without explanations. Check Vertex AI status and the worker's `posternModelCaller` role. |
| `prompt_rejected` | A prompt held something shaped like a credential and wasn't sent | Treat as an incident: find where the secret-shaped text came from. |
| (no label, worker or scheduler error) | The worker crashed, or the scheduler couldn't start it | Read the job's logs. A crashed run resumes from its checkpoints on the next execution. |

## Connect a project

1. Create the Postern user (until Phase 8's sign-in):
   ```bash
   uv run postern users create --email <owner email>
   ```
2. Issue a single-use link (valid 60 minutes) and send it to the owner:
   ```bash
   uv run postern connect link --user <user_id> --project-ref <ref> --api-url https://postern-api-3nsmysl4ka-uc.a.run.app
   ```
3. The owner opens it, signs in to Supabase, and approves the three read-only scopes. Postern
   refuses projects that aren't in the owner's own account, and projects with temporary
   database access turned on.
4. Store the project's public key. The prompt is hidden; secret and `service_role` keys are
   refused, and so is a key for any other project:
   ```bash
   uv run postern connect key --connection <connection_id>
   ```

## Credentials

**Component database logins** (`postern_api`, `postern_worker`). This sets a new password,
sent to Postgres only as a SCRAM verifier, writes the connection string to Secret Manager,
and checks the login:

```bash
uv run postern db-login rotate --role postern_worker --gcp-project $P --gcloud $G
```

Cloud Run reads `latest` when an instance or execution starts. For the API, deploy a new
revision afterwards; the worker picks it up on its next execution.

**Token-encryption key.** Created once:

```bash
uv run postern secrets init-token-key --gcp-project $P --gcloud $G
```

The command refuses if a key already exists. Postern can't rotate this key yet; replacing it
would make every stored OAuth token unreadable, so every owner would have to reconnect.

## Kill test

The kill test runs in CI (`tests/invariants/test_checkpoints.py`). To repeat it by hand
against production, label it a **game day** everywhere:

1. Enqueue a run.
2. Start the worker, and cancel the execution while it's running:
   ```bash
   & $G run jobs executions cancel <execution> --project $P --region $R
   ```
3. Run the worker again.
4. `postern replay <run_id>` should show one `run.resumed`, and each finding exactly once.
