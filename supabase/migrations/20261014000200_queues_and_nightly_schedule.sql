-- Phase 2: one pgmq queue per tier, and the nightly pg_cron schedule (ADR-002, ADR-011).
-- Both are created through extension functions, which schema diffs don't capture, so they
-- live only here.

-- Queues: tier0, tier1, tier2, meta. Idempotent.
select pgmq.create(wanted.name)
from unnest(array['tier0', 'tier1', 'tier2', 'meta']) as wanted (name)
where not exists (select 1 from pgmq.list_queues() q where q.queue_name = wanted.name);

-- The queue tables belong to Postern's own components only. They are reached through
-- SECURITY DEFINER functions, never directly and never through the Data API.
do $$
declare
  queue_table text;
begin
  for queue_table in
    select format('pgmq.%I', c.relname)
      from pg_catalog.pg_class c
      join pg_catalog.pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'pgmq'
       and c.relkind = 'r'
       and c.relname in ('q_tier0', 'q_tier1', 'q_tier2', 'q_meta',
                         'a_tier0', 'a_tier1', 'a_tier2', 'a_meta')
  loop
    execute format('alter table %s enable row level security', queue_table);
    execute format('revoke all on %s from public, anon, authenticated', queue_table);
  end loop;
end
$$;

-- Nightly at 02:17 UTC (07:47 IST): one run per active nightly agent per active project.
-- cron.schedule with a job name replaces any existing job of that name.
select cron.schedule(
  'postern-nightly-runs',
  '17 2 * * *',
  $$select postern.enqueue_nightly_runs()$$
);
