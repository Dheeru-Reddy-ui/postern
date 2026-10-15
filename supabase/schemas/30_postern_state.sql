-- Run state machine and the only write paths into the control plane (ADR-011).
--
-- queued -> running | failed
-- running -> awaiting_human | completed | failed
-- awaiting_human -> running | completed | failed | rolled_back
-- completed, failed, rolled_back are terminal.
--
-- Every function that changes state writes its audit entry in the same transaction, and a
-- deferred constraint trigger rejects any run status change without one (invariant 8).

create function postern.run_transition_allowed(
  p_from postern.run_status,
  p_to postern.run_status
)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select (p_from::text, p_to::text) in (
    ('queued', 'running'), ('queued', 'failed'),
    ('running', 'awaiting_human'), ('running', 'completed'), ('running', 'failed'),
    ('awaiting_human', 'running'), ('awaiting_human', 'completed'),
    ('awaiting_human', 'failed'), ('awaiting_human', 'rolled_back')
  )
$$;

create function postern.queue_for_tier(p_tier smallint)
returns text
language sql
immutable
set search_path = ''
as $$
  select case p_tier when 0 then 'tier0' when 1 then 'tier1' when 2 then 'tier2' else 'meta' end
$$;

-- ------------------------------------------------------------- runs: guard triggers

create function postern.runs_guard_insert()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  agent_row postern.agents;
begin
  select * into agent_row from postern.agents a where a.name = new.agent;
  if not found then
    raise exception 'agent % is not registered', new.agent using errcode = 'foreign_key_violation';
  end if;
  if agent_row.status <> 'active' then
    raise exception 'agent % is %, not active', new.agent, agent_row.status
      using errcode = 'check_violation';
  end if;
  if new.status <> 'queued' then
    raise exception 'a run must start queued, not %', new.status using errcode = 'check_violation';
  end if;
  new.tier := agent_row.tier;
  new.manifest_hash := agent_row.manifest_hash;
  return new;
end
$$;

create trigger runs_guard_insert
  before insert on postern.runs
  for each row execute function postern.runs_guard_insert();

create function postern.runs_guard_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.id <> old.id or new.agent <> old.agent or new.tier <> old.tier
     or new.manifest_hash <> old.manifest_hash or new.idempotency_key <> old.idempotency_key
     or new.trigger <> old.trigger or new.created_at <> old.created_at then
    raise exception 'run % identity columns are immutable', old.id using errcode = 'check_violation';
  end if;

  if new.status is distinct from old.status then
    if not postern.run_transition_allowed(old.status, new.status) then
      raise exception 'illegal run transition % -> % for run %', old.status, new.status, old.id
        using errcode = 'check_violation';
    end if;
    -- TM-24: a human-gated agent's run can't finish successfully without a human's approval.
    if new.status = 'completed'
       and (select a.human_gate from postern.agents a where a.name = new.agent)
       and new.human_decision is distinct from 'approved' then
      raise exception 'run % needs an approved human decision before it can complete', old.id
        using errcode = 'check_violation';
    end if;
  end if;

  new.updated_at := now();
  return new;
end
$$;

create trigger runs_guard_update
  before update on postern.runs
  for each row execute function postern.runs_guard_update();

-- Checked at commit: a run status change must be in the audit log of the same transaction.
create function postern.runs_require_audit()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' or new.status is distinct from old.status then
    if not exists (
      select 1 from postern.audit_log a
      where a.txid = txid_current()
        and a.run_id = new.id
        and a.event_type in ('run.enqueued', 'run.transitioned')
        and a.payload ->> 'to' = new.status::text
    ) then
      raise exception 'run % moved to % without an audit entry in the same transaction', new.id,
        new.status using errcode = 'check_violation';
    end if;
  end if;
  return null;
end
$$;

create constraint trigger runs_require_audit
  after insert or update on postern.runs
  deferrable initially deferred
  for each row execute function postern.runs_require_audit();

-- -------------------------------------------------------------------- write paths

-- Enqueue a run for a registered, active agent. Idempotent: a repeated idempotency_key
-- returns the existing run and creates nothing (TM-18).
create function postern.enqueue_run(
  p_agent text,
  p_trigger text,
  p_idempotency_key text,
  p_connection_id uuid default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  agent_row postern.agents;
  owner_id uuid;
  new_run uuid;
begin
  select * into agent_row from postern.agents a where a.name = p_agent;
  if not found then
    raise exception 'agent % is not registered', p_agent using errcode = 'foreign_key_violation';
  end if;
  if agent_row.status <> 'active' then
    raise exception 'agent % is %, not active', p_agent, agent_row.status
      using errcode = 'check_violation';
  end if;

  if p_connection_id is not null then
    select c.user_id into owner_id
      from postern.connections c
      where c.id = p_connection_id and c.status = 'active';
    if not found then
      raise exception 'connection % is not active', p_connection_id
        using errcode = 'check_violation';
    end if;
  end if;

  insert into postern.runs (agent, tier, manifest_hash, connection_id, user_id, trigger,
                            idempotency_key)
  values (p_agent, agent_row.tier, agent_row.manifest_hash, p_connection_id, owner_id,
          p_trigger, p_idempotency_key)
  on conflict (idempotency_key) do nothing
  returning id into new_run;

  if new_run is null then
    select r.id into new_run from postern.runs r where r.idempotency_key = p_idempotency_key;
    return new_run;
  end if;

  perform postern.write_audit(
    'run.enqueued', new_run, 'run', new_run::text,
    jsonb_build_object(
      'to', 'queued', 'agent', p_agent, 'tier', agent_row.tier,
      'manifest_hash', agent_row.manifest_hash, 'trigger', p_trigger,
      'idempotency_key', p_idempotency_key, 'connection_id', p_connection_id,
      'user_id', owner_id
    )
  );
  perform postern.emit_event('runs', jsonb_build_object(
    'run_id', new_run, 'agent', p_agent, 'tier', agent_row.tier, 'from_status', null,
    'to_status', 'queued', 'trigger', p_trigger, 'idempotency_key', p_idempotency_key,
    'attempt', 0, 'error_code', null, 'user_id', owner_id, 'connection_id', p_connection_id
  ));
  perform pgmq.send(postern.queue_for_tier(agent_row.tier), jsonb_build_object('run_id', new_run));
  return new_run;
end
$$;

create function postern.transition_run(
  p_run_id uuid,
  p_to postern.run_status,
  p_error_code text default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_status postern.run_status;
  run_row postern.runs;
begin
  select r.status into current_status from postern.runs r where r.id = p_run_id for update;
  if not found then
    raise exception 'run % does not exist', p_run_id using errcode = 'no_data_found';
  end if;

  update postern.runs r
     set status = p_to,
         error_code = coalesce(p_error_code, r.error_code),
         attempt = case when p_to = 'running' then r.attempt + 1 else r.attempt end,
         started_at = case when p_to = 'running' and r.started_at is null then now()
                           else r.started_at end,
         finished_at = case when p_to in ('completed', 'failed', 'rolled_back') then now()
                            else r.finished_at end
   where r.id = p_run_id
  returning * into run_row;

  perform postern.write_audit(
    'run.transitioned', p_run_id, 'run', p_run_id::text,
    jsonb_build_object('from', current_status, 'to', p_to, 'error_code', p_error_code)
  );
  perform postern.emit_event('runs', jsonb_build_object(
    'run_id', p_run_id, 'agent', run_row.agent, 'tier', run_row.tier,
    'from_status', current_status, 'to_status', p_to, 'trigger', run_row.trigger,
    'idempotency_key', run_row.idempotency_key, 'attempt', run_row.attempt,
    'error_code', p_error_code, 'user_id', run_row.user_id,
    'connection_id', run_row.connection_id
  ));
end
$$;

-- A human approves or declines an awaiting_human run. If the run belongs to a connection,
-- only that connection's owner may decide (TM-24).
create function postern.record_human_decision(
  p_run_id uuid,
  p_decision text,
  p_decided_by uuid
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  run_row postern.runs;
begin
  select * into run_row from postern.runs r where r.id = p_run_id for update;
  if not found then
    raise exception 'run % does not exist', p_run_id using errcode = 'no_data_found';
  end if;
  if run_row.status <> 'awaiting_human' then
    raise exception 'run % is %, not awaiting_human', p_run_id, run_row.status
      using errcode = 'check_violation';
  end if;
  if p_decision not in ('approved', 'declined') then
    raise exception 'decision must be approved or declined' using errcode = 'check_violation';
  end if;
  if run_row.user_id is not null and p_decided_by is distinct from run_row.user_id then
    raise exception 'only the owner of run %''s connection can decide', p_run_id
      using errcode = 'insufficient_privilege';
  end if;

  update postern.runs r
     set human_decision = p_decision, human_decided_by = p_decided_by, human_decided_at = now()
   where r.id = p_run_id;

  perform postern.write_audit(
    'human.decided', p_run_id, 'run', p_run_id::text,
    jsonb_build_object('decision', p_decision, 'decided_by', p_decided_by)
  );
end
$$;

-- One checkpoint per step, with its audit entry (replay rebuilds steps from these).
create function postern.record_step(
  p_run_id uuid,
  p_step_index integer,
  p_kind text,
  p_tool_name text,
  p_status text,
  p_input_hash text,
  p_output_hash text,
  p_refusal_reason text default null,
  p_duration_ms integer default null,
  p_output jsonb default null
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  run_row postern.runs;
  step_id bigint;
begin
  select * into run_row from postern.runs r where r.id = p_run_id;
  if not found or run_row.status <> 'running' then
    raise exception 'run % is not running', p_run_id using errcode = 'check_violation';
  end if;

  insert into postern.steps (run_id, step_index, kind, tool_name, status, refusal_reason,
                             input_hash, output_hash, output, duration_ms)
  values (p_run_id, p_step_index, p_kind, p_tool_name, p_status, p_refusal_reason,
          p_input_hash, p_output_hash, p_output, p_duration_ms)
  returning id into step_id;

  perform postern.write_audit(
    'step.recorded', p_run_id, 'step', step_id::text,
    jsonb_build_object(
      'step_index', p_step_index, 'kind', p_kind, 'tool_name', p_tool_name,
      'status', p_status, 'refusal_reason', p_refusal_reason,
      'input_hash', p_input_hash, 'output_hash', p_output_hash, 'duration_ms', p_duration_ms
    )
  );
  perform postern.emit_event('steps', jsonb_build_object(
    'run_id', p_run_id, 'agent', run_row.agent, 'step_index', p_step_index,
    'step_kind', p_kind, 'tool_name', p_tool_name, 'status', p_status,
    'refusal_reason', p_refusal_reason, 'input_hash', p_input_hash,
    'output_hash', p_output_hash, 'duration_ms', p_duration_ms,
    'user_id', run_row.user_id, 'connection_id', run_row.connection_id
  ));
  return step_id;
end
$$;

-- ---------------------------------------------------------------- registry sync

-- Upsert one agent from the compiled registry. Operator only: changing an agent's grants is
-- a consequential decision, so it is audited.
create function postern.sync_agent(
  p_name text,
  p_owner text,
  p_tier smallint,
  p_tools text[],
  p_human_gate boolean,
  p_notification_budget jsonb,
  p_schedule text,
  p_status postern.agent_status,
  p_manifest jsonb,
  p_manifest_hash text,
  p_registry_version integer
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  previous_hash text;
  outcome text;
begin
  select a.manifest_hash into previous_hash from postern.agents a where a.name = p_name;
  if not found then
    outcome := 'registered';
  elsif previous_hash = p_manifest_hash then
    outcome := 'unchanged';
  else
    outcome := 'updated';
  end if;

  insert into postern.agents (name, owner, tier, tools, human_gate, notification_budget,
                              schedule, status, manifest, manifest_hash, registry_version,
                              synced_at)
  values (p_name, p_owner, p_tier, p_tools, p_human_gate, p_notification_budget, p_schedule,
          p_status, p_manifest, p_manifest_hash, p_registry_version, now())
  on conflict (name) do update
    set owner = excluded.owner, tier = excluded.tier, tools = excluded.tools,
        human_gate = excluded.human_gate, notification_budget = excluded.notification_budget,
        schedule = excluded.schedule, status = excluded.status, manifest = excluded.manifest,
        manifest_hash = excluded.manifest_hash, registry_version = excluded.registry_version,
        synced_at = excluded.synced_at;

  if outcome <> 'unchanged' then
    perform postern.write_audit(
      'agent.' || outcome, null, 'agent', p_name,
      jsonb_build_object(
        'tier', p_tier, 'tools', to_jsonb(p_tools), 'human_gate', p_human_gate,
        'status', p_status, 'manifest_hash', p_manifest_hash, 'previous_hash', previous_hash
      )
    );
  end if;
  return outcome;
end
$$;

-- Agents no longer in the registry stop being runnable; their history stays.
create function postern.retire_agents_not_in(p_names text[])
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  retired_name text;
  retired_count integer := 0;
begin
  for retired_name in
    update postern.agents a set status = 'retired', synced_at = now()
     where a.name <> all (p_names) and a.status <> 'retired'
    returning a.name
  loop
    perform postern.write_audit('agent.retired', null, 'agent', retired_name, '{}'::jsonb);
    retired_count := retired_count + 1;
  end loop;
  return retired_count;
end
$$;

-- -------------------------------------------------------------- nightly schedule

-- Called by pg_cron. One run per active nightly agent per active Supabase connection per
-- day; the idempotency key makes a second call on the same day a no-op.
create function postern.enqueue_nightly_runs()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  pair record;
  enqueued integer := 0;
begin
  for pair in
    select a.name as agent, c.id as connection_id
      from postern.agents a
      cross join postern.connections c
     where a.status = 'active' and a.schedule = 'nightly'
       and c.status = 'active' and c.kind = 'supabase_project'
  loop
    perform postern.enqueue_run(
      pair.agent, 'schedule',
      format('nightly:%s:%s:%s', pair.agent, pair.connection_id,
             to_char(now() at time zone 'UTC', 'YYYY-MM-DD')),
      pair.connection_id
    );
    enqueued := enqueued + 1;
  end loop;
  return enqueued;
end
$$;
