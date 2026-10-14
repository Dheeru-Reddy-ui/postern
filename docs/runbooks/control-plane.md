# Runbook: Postern's control plane

The control plane is the `postern` schema on Postern's own Supabase project
(`postern-control`, ref `bpymxbzldxhsqrvrybyo`). Its design is in
[ADR-011](../adr/ADR-011-control-plane-data-model.md). Run every command from the repo root.
Commands are PowerShell.

## Connect as the operator

The operator connection uses Postern's own database password from Terraform state, through
the session pooler. It is never printed.

```bash
$pw = terraform -chdir=infra/terraform/supabase output -raw database_password
$env:POSTERN_DATABASE_URL = (terraform -chdir=infra/terraform/supabase output -json pooler_urls | ConvertFrom-Json).transaction -replace '\[YOUR-PASSWORD\]', $pw -replace ':6543/', ':5432/'
Remove-Variable pw
```

Clear it when you're done:

```bash
Remove-Item Env:POSTERN_DATABASE_URL
```

## Apply migrations

Edit the declarative files in `supabase/schemas/`, then write a migration in
`supabase/migrations/` and review it. Roles, queues and cron jobs exist only in migrations
(ADR-011).

1. Check what will run:
   ```bash
   npx supabase@2.120.0 db push --db-url $env:POSTERN_DATABASE_URL --dry-run
   ```
2. **Show the dry run to Dheeru and wait for approval.**
3. Apply:
   ```bash
   npx supabase@2.120.0 db push --db-url $env:POSTERN_DATABASE_URL
   ```

CI applies every migration to a fresh local database and runs the invariant tests before
anything reaches production.

## Sync the agent registry

```bash
uv run postern registry validate
uv run postern registry compile --check
uv run postern registry sync
```

`sync` prints `registered`, `updated` or `unchanged` for each agent, and retires agents that
were removed from the registry. Every change is written to the audit log.

## Drill: watch a fake run move through its states (GAME DAY)

This uses `drill_echo`, an agent with no tools that exists only for drills. Every audit row it
creates is permanent and labelled by agent `drill_echo` and trigger `drill`.

Run these one at a time in the Supabase dashboard's SQL editor for `postern-control`.
Replace `RUN_ID` with the id the first statement returns.

```sql
-- 1. Enqueue. Returns the new run's id; the run starts as queued.
select postern.enqueue_run('drill_echo', 'drill', 'game-day:' || now()::text) as run_id;

-- 2. Watch it (repeat after every step).
select status, attempt, human_decision, started_at, finished_at
from postern.runs where id = 'RUN_ID';

-- 3. A worker picks it up.
select postern.transition_run('RUN_ID', 'running');

-- 4. One checkpointed step.
select postern.record_step('RUN_ID', 0, 'final', null, 'completed',
  encode(sha256('drill input'::bytea), 'hex'), encode(sha256('drill output'::bytea), 'hex'));

-- 5. drill_echo is human-gated, so it waits for a person.
select postern.transition_run('RUN_ID', 'awaiting_human');

-- 6. Expect an ERROR: it can't complete without approval.
select postern.transition_run('RUN_ID', 'completed');

-- 7. Approve (drill runs have no connection, so the operator decides), then complete.
select postern.record_human_decision('RUN_ID', 'approved', null);
select postern.transition_run('RUN_ID', 'completed');

-- 8. Expect an ERROR: completed is final.
select postern.transition_run('RUN_ID', 'running');

-- 9. The history, straight from the append-only audit log.
select chain_position, event_type, actor, payload
from postern.audit_log where run_id = 'RUN_ID' order by chain_position;

-- 10. No worker consumes drill messages yet (Phase 3), so archive this one.
select pgmq.archive('tier0', msg_id) from pgmq.q_tier0 where message ->> 'run_id' = 'RUN_ID';
```

## Replay a run from the audit log alone

```bash
uv run postern replay RUN_ID
```
```bash
uv run postern replay RUN_ID --json
```

Replay reads only `postern.audit_log`. It checks every row's hash in Python, asks the database
to verify the whole chain, and exits with code 2 if anything doesn't check out.

## Verify the whole audit chain

```bash
uv run postern audit verify
```

Any output other than "audit chain intact" is an incident.

## Local development

```bash
npx supabase@2.120.0 db start
```
```bash
$env:POSTERN_TEST_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"; uv run pytest
```

Postern's local Supabase ports are 55320–55329, so they don't clash with another project's
local stack on the default 543xx ports. `npx supabase@2.120.0 db reset --local` reapplies
every migration to an empty database.
