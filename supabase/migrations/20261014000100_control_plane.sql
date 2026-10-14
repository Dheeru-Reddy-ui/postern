-- Phase 2: the control plane (ADR-011).
-- Generated verbatim from supabase/schemas/10_..40_*.sql, the declarative source of truth;
-- production had no postern schema before this migration.

-- >>> supabase/schemas/10_postern_core.sql

-- Postern control plane: schema, types and tables (Phase 2, ADR-011).
--
-- Everything lives in the private `postern` schema, which the Data API does not expose.
-- Components never write these tables directly: every state change goes through a
-- SECURITY DEFINER function in 30_state.sql that writes the audit log in the same
-- transaction (invariant 8).

create schema if not exists postern;
comment on schema postern is
  'Postern control plane. Not exposed through the Data API; written only through functions.';

create type postern.run_status as enum (
  'queued', 'running', 'awaiting_human', 'completed', 'failed', 'rolled_back'
);

create type postern.connection_kind as enum ('supabase_project', 'github_installation');

create type postern.agent_status as enum ('active', 'planned', 'retired');

-- ---------------------------------------------------------------------------- users

-- One row per person, keyed by their Supabase Auth user on Postern's own project
-- (sign in with GitHub, Phase 8).
create table postern.users (
  id uuid primary key references auth.users (id) on delete cascade,
  timezone text not null default 'UTC',
  created_at timestamptz not null default now()
);
comment on table postern.users is 'People who use Postern. Contact details arrive in Phase 6.';

-- ---------------------------------------------------------------------- connections

create table postern.connections (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references postern.users (id) on delete cascade,
  kind postern.connection_kind not null,
  supabase_project_ref text,
  github_installation_id bigint,
  status text not null default 'active' check (status in ('active', 'revoked', 'error')),
  -- OAuth tokens arrive already envelope-encrypted by postern-api (TM-05). The check below
  -- rejects anything without Postern's envelope header, so a plaintext token can't be
  -- stored by mistake.
  access_token_ciphertext bytea,
  refresh_token_ciphertext bytea,
  token_key_version text,
  token_expires_at timestamptz,
  verified_at timestamptz,
  last_checked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint connection_kind_fields check (
    (kind = 'supabase_project' and supabase_project_ref is not null
      and github_installation_id is null)
    or (kind = 'github_installation' and github_installation_id is not null
      and supabase_project_ref is null)
  ),
  constraint supabase_project_ref_format check (
    supabase_project_ref is null or supabase_project_ref ~ '^[a-z]{20}$'
  ),
  constraint tokens_are_envelope_ciphertext check (
    (access_token_ciphertext is null
      or substring(access_token_ciphertext from 1 for 4) = '\x70737431'::bytea)
    and (refresh_token_ciphertext is null
      or substring(refresh_token_ciphertext from 1 for 4) = '\x70737431'::bytea)
  )
);
comment on table postern.connections is
  'A Supabase project or GitHub installation a user connected. Tokens are ciphertext only.';

create unique index connections_one_per_project
  on postern.connections (user_id, supabase_project_ref) where kind = 'supabase_project';
create unique index connections_one_per_installation
  on postern.connections (user_id, github_installation_id) where kind = 'github_installation';

-- --------------------------------------------------------------------------- agents

-- Synced from agents/registry.yaml by `postern registry sync` (operator only).
create table postern.agents (
  name text primary key check (name ~ '^[a-z][a-z0-9_]{2,62}$'),
  owner text not null check (owner <> ''),
  tier smallint not null check (tier between 0 and 3),
  tools text[] not null,
  human_gate boolean not null,
  notification_budget jsonb not null,
  schedule text check (schedule in ('nightly', 'daily')),
  status postern.agent_status not null,
  manifest jsonb not null,
  manifest_hash text not null check (manifest_hash ~ '^[0-9a-f]{64}$'),
  registry_version integer not null,
  synced_at timestamptz not null default now()
);
comment on table postern.agents is
  'Registered agents and their compiled tool manifests. Unregistered agents cannot run.';

-- ----------------------------------------------------------------------------- runs

create table postern.runs (
  id uuid primary key default gen_random_uuid(),
  agent text not null references postern.agents (name),
  tier smallint not null,
  manifest_hash text not null,
  connection_id uuid references postern.connections (id) on delete set null,
  user_id uuid references postern.users (id) on delete cascade,
  status postern.run_status not null default 'queued',
  trigger text not null check (trigger in ('schedule', 'webhook', 'human', 'eval', 'drill')),
  idempotency_key text not null unique,
  attempt integer not null default 0,
  error_code text,
  human_decision text check (human_decision in ('approved', 'declined')),
  human_decided_by uuid references postern.users (id),
  human_decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);
comment on table postern.runs is
  'One agent run. Status moves only along the state machine in 30_state.sql.';

create index runs_status on postern.runs (status);
create index runs_connection on postern.runs (connection_id);

-- ---------------------------------------------------------------------------- steps

create table postern.steps (
  id bigint generated always as identity primary key,
  run_id uuid not null references postern.runs (id) on delete cascade,
  step_index integer not null check (step_index >= 0),
  kind text not null check (kind in ('llm_call', 'tool_call', 'final')),
  tool_name text,
  status text not null check (status in ('completed', 'refused', 'failed')),
  refusal_reason text,
  input_hash text not null check (input_hash ~ '^[0-9a-f]{64}$'),
  output_hash text check (output_hash ~ '^[0-9a-f]{64}$'),
  duration_ms integer check (duration_ms >= 0),
  created_at timestamptz not null default now(),
  unique (run_id, step_index)
);
comment on table postern.steps is 'One checkpoint per agent step; a resumed run skips these.';

-- ------------------------------------------------------------------------- findings

create table postern.findings (
  id uuid primary key default gen_random_uuid(),
  connection_id uuid not null references postern.connections (id) on delete cascade,
  user_id uuid not null references postern.users (id) on delete cascade,
  run_id uuid references postern.runs (id) on delete set null,
  agent text not null references postern.agents (name),
  fingerprint text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
  finding_type text not null,
  category text not null check (category in ('security', 'health', 'performance')),
  severity text not null check (severity in ('critical', 'high', 'medium', 'low', 'info')),
  source text not null
    check (source in ('advisor', 'splinter_fallback', 'probe', 'introspection')),
  object_schema text,
  object_name text,
  probe_http_status integer,
  -- Invariant 4: a probe records a count, capped at 1000, never rows.
  probe_row_count integer check (probe_row_count between 0 and 1000),
  probe_confirmed boolean not null default false,
  status text not null default 'open' check (
    status in ('open', 'proposed', 'fixed', 'verified_closed', 'dismissed', 'reopened')
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- TM-14: only a probe-confirmed live exposure may be critical.
  constraint critical_requires_confirmed_probe check (severity <> 'critical' or probe_confirmed),
  unique (connection_id, fingerprint)
);
comment on table postern.findings is 'Problems found in a connected project. No row data, ever.';

-- ------------------------------------------------------------------------ proposals

create table postern.proposals (
  id uuid primary key default gen_random_uuid(),
  finding_id uuid not null references postern.findings (id) on delete cascade,
  fingerprint text not null,
  connection_id uuid not null references postern.connections (id) on delete cascade,
  user_id uuid not null references postern.users (id) on delete cascade,
  run_id uuid references postern.runs (id) on delete set null,
  status text not null check (
    status in ('drafting', 'validated', 'pr_open', 'merged', 'declined', 'rolled_back', 'failed')
  ),
  -- Invariant 3 at the data layer: Postern only ever names postern/* branches.
  branch text check (branch is null or branch ~ '^postern/[A-Za-z0-9._/-]+$'),
  pr_number integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on table postern.proposals is 'A drafted fix for one finding (Phase 5).';

-- TM-18: at most one open proposal per finding fingerprint.
create unique index proposals_one_open_per_fingerprint
  on postern.proposals (fingerprint) where status in ('drafting', 'validated', 'pr_open');

-- -------------------------------------------------------------------- notifications

create table postern.notifications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references postern.users (id) on delete cascade,
  channel text not null check (channel in ('email', 'slack', 'mcp')),
  kind text not null check (kind in ('realtime_alert', 'weekly_digest', 'transactional')),
  -- TM-18: the same message can't be recorded twice.
  dedupe_key text not null unique,
  finding_id uuid references postern.findings (id) on delete set null,
  status text not null check (status in ('queued', 'sent', 'suppressed', 'failed')),
  suppression_reason text check (suppression_reason in ('budget', 'quiet_hours', 'duplicate')),
  template text not null,
  template_version text not null,
  provider_message_id text,
  created_at timestamptz not null default now(),
  sent_at timestamptz
);
comment on table postern.notifications is
  'Every message decision from the notification service (Phase 6).';

-- --------------------------------------------------------------------------- ledger

create table postern.ledger (
  id bigint generated always as identity primary key,
  finding_id uuid references postern.findings (id) on delete cascade,
  fingerprint text not null,
  user_id uuid not null references postern.users (id) on delete cascade,
  stage text not null check (
    stage in ('found', 'proposed', 'merged', 'verified_closed', 'declined', 'rolled_back')
  ),
  issue_type text not null,
  minutes_absorbed numeric check (minutes_absorbed >= 0),
  value_model_version text,
  proposal_id uuid references postern.proposals (id) on delete set null,
  recorded_at timestamptz not null default now()
);
comment on table postern.ledger is
  'Value ledger: found, proposed, merged, verified closed (Phase 7).';

-- >>> supabase/schemas/20_postern_audit.sql

-- Append-only, hash-chained audit log (invariant 8, TM-22, ADR-011).
--
-- * chain_position is assigned under a transaction-scoped advisory lock, so writers form one
--   line even when several transactions insert at once.
-- * row_hash = sha256(prev_hash || canonical text of the row). Editing, inserting or removing
--   a row in the middle breaks every hash after it (postern.verify_audit_chain).
-- * UPDATE, DELETE and TRUNCATE are revoked from every role (50_security.sql) and blocked by
--   triggers, which also stop the table owner.

create table postern.audit_log (
  chain_position bigint primary key,
  occurred_at timestamptz not null,
  txid bigint not null,
  actor text not null,
  event_type text not null check (event_type ~ '^[a-z_]+\.[a-z_]+$'),
  run_id uuid,
  entity_type text,
  entity_id text,
  payload jsonb not null default '{}'::jsonb,
  prev_hash bytea not null,
  row_hash bytea not null unique
);
comment on table postern.audit_log is
  'Append-only, hash-chained record of every consequential decision. No foreign keys: it outlives what it describes.';

create index audit_log_run on postern.audit_log (run_id, chain_position);
create index audit_log_txid on postern.audit_log (txid);

-- The hash the first row chains from.
create function postern.audit_genesis_hash()
returns bytea
language sql
immutable
set search_path = ''
as $$
  select sha256(convert_to('postern-audit-genesis-v1', 'UTF8'))
$$;

-- Canonical text of a row, also recomputed independently by postern/core/audit.py.
-- Fields are joined with the ASCII unit separator (0x1f), which can't appear in jsonb text
-- unescaped.
create function postern.audit_row_text(
  p_chain_position bigint,
  p_occurred_at timestamptz,
  p_txid bigint,
  p_actor text,
  p_event_type text,
  p_run_id uuid,
  p_entity_type text,
  p_entity_id text,
  p_payload jsonb
)
returns text
language sql
stable
set search_path = ''
as $$
  select concat_ws(
    chr(31),
    p_chain_position::text,
    to_char(p_occurred_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    p_txid::text,
    p_actor,
    p_event_type,
    coalesce(p_run_id::text, ''),
    coalesce(p_entity_type, ''),
    coalesce(p_entity_id, ''),
    p_payload::text
  )
$$;

create function postern.audit_row_hash(
  p_chain_position bigint,
  p_occurred_at timestamptz,
  p_txid bigint,
  p_actor text,
  p_event_type text,
  p_run_id uuid,
  p_entity_type text,
  p_entity_id text,
  p_payload jsonb,
  p_prev_hash bytea
)
returns bytea
language sql
stable
set search_path = ''
as $$
  select sha256(
    p_prev_hash || convert_to(
      postern.audit_row_text(
        p_chain_position, p_occurred_at, p_txid, p_actor, p_event_type,
        p_run_id, p_entity_type, p_entity_id, p_payload
      ),
      'UTF8'
    )
  )
$$;

-- Fills the chain columns. Callers can't choose position, time, txid or hashes.
create function postern.audit_log_chain()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  last_position bigint;
  last_hash bytea;
begin
  -- One writer at a time, until commit: the lock is released with the transaction.
  perform pg_advisory_xact_lock(7364726531);
  select a.chain_position, a.row_hash
    into last_position, last_hash
    from postern.audit_log a
    order by a.chain_position desc
    limit 1;
  new.chain_position := coalesce(last_position, 0) + 1;
  new.prev_hash := coalesce(last_hash, postern.audit_genesis_hash());
  new.occurred_at := now();
  new.txid := txid_current();
  new.row_hash := postern.audit_row_hash(
    new.chain_position, new.occurred_at, new.txid, new.actor, new.event_type,
    new.run_id, new.entity_type, new.entity_id, new.payload, new.prev_hash
  );
  return new;
end
$$;

create trigger audit_log_chain
  before insert on postern.audit_log
  for each row execute function postern.audit_log_chain();

create function postern.audit_log_is_append_only()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'postern.audit_log is append-only: % is not allowed', tg_op
    using errcode = 'insufficient_privilege';
end
$$;

create trigger audit_log_no_update_or_delete
  before update or delete on postern.audit_log
  for each row execute function postern.audit_log_is_append_only();

create trigger audit_log_no_truncate
  before truncate on postern.audit_log
  for each statement execute function postern.audit_log_is_append_only();

-- The only way components add audit entries. actor is the session's login role, which a
-- caller can't spoof from inside a SECURITY DEFINER function.
create function postern.write_audit(
  p_event_type text,
  p_run_id uuid,
  p_entity_type text,
  p_entity_id text,
  p_payload jsonb
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_position bigint;
begin
  insert into postern.audit_log (occurred_at, txid, actor, event_type, run_id, entity_type,
                                 entity_id, payload, prev_hash, row_hash)
  values (now(), 0, session_user::text, p_event_type, p_run_id, p_entity_type, p_entity_id,
          coalesce(p_payload, '{}'::jsonb), ''::bytea, ''::bytea)
  returning chain_position into v_position;
  return v_position;
end
$$;

-- Every row whose position, link or contents don't check out. Empty result = intact chain.
create function postern.verify_audit_chain()
returns table (chain_position bigint, problem text)
language sql
stable
set search_path = ''
as $$
  with ordered as (
    select
      a.*,
      lag(a.chain_position) over w as previous_position,
      lag(a.row_hash) over w as previous_hash
    from postern.audit_log a
    window w as (order by a.chain_position)
  )
  select o.chain_position,
    case
      when o.chain_position <> coalesce(o.previous_position, 0) + 1
        then 'gap before this row (a row was removed or inserted)'
      when o.prev_hash <> coalesce(o.previous_hash, postern.audit_genesis_hash())
        then 'prev_hash does not match the previous row'
      else 'row_hash does not match the row contents (the row was edited)'
    end
  from ordered o
  where o.chain_position <> coalesce(o.previous_position, 0) + 1
     or o.prev_hash <> coalesce(o.previous_hash, postern.audit_genesis_hash())
     or o.row_hash <> postern.audit_row_hash(
          o.chain_position, o.occurred_at, o.txid, o.actor, o.event_type,
          o.run_id, o.entity_type, o.entity_id, o.payload, o.prev_hash)
  order by o.chain_position
$$;

-- >>> supabase/schemas/30_postern_state.sql

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
   where r.id = p_run_id;

  perform postern.write_audit(
    'run.transitioned', p_run_id, 'run', p_run_id::text,
    jsonb_build_object('from', current_status, 'to', p_to, 'error_code', p_error_code)
  );
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
  p_duration_ms integer default null
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  step_id bigint;
begin
  if not exists (select 1 from postern.runs r where r.id = p_run_id and r.status = 'running') then
    raise exception 'run % is not running', p_run_id using errcode = 'check_violation';
  end if;

  insert into postern.steps (run_id, step_index, kind, tool_name, status, refusal_reason,
                             input_hash, output_hash, duration_ms)
  values (p_run_id, p_step_index, p_kind, p_tool_name, p_status, p_refusal_reason,
          p_input_hash, p_output_hash, p_duration_ms)
  returning id into step_id;

  perform postern.write_audit(
    'step.recorded', p_run_id, 'step', step_id::text,
    jsonb_build_object(
      'step_index', p_step_index, 'kind', p_kind, 'tool_name', p_tool_name,
      'status', p_status, 'refusal_reason', p_refusal_reason,
      'input_hash', p_input_hash, 'output_hash', p_output_hash, 'duration_ms', p_duration_ms
    )
  );
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

-- >>> supabase/schemas/40_postern_security.sql

-- Privileges and row-level security for the control plane (ADR-011, TM-22, TM-25).
--
-- Roles (created in a migration, since roles live outside any schema):
--   postern_api     postern-api (webhooks, OAuth callbacks, human decisions)
--   postern_worker  postern-worker (runs agents)
--   authenticated   a signed-in person using Postern's own web pages (Phase 8)
-- Components read through grants plus explicit RLS policies and write only through the
-- SECURITY DEFINER functions in 20_ and 30_.

-- ------------------------------------------------------------------ row-level security

alter table postern.users enable row level security;
alter table postern.connections enable row level security;
alter table postern.agents enable row level security;
alter table postern.runs enable row level security;
alter table postern.steps enable row level security;
alter table postern.audit_log enable row level security;
alter table postern.findings enable row level security;
alter table postern.proposals enable row level security;
alter table postern.notifications enable row level security;
alter table postern.ledger enable row level security;

-- --------------------------------------------------------------- start from nothing

revoke all on schema postern from public;
revoke all on all tables in schema postern from public, anon, authenticated, service_role;
revoke all on all sequences in schema postern from public, anon, authenticated, service_role;
-- Functions are executable by PUBLIC by default; nothing here should be.
revoke all on all functions in schema postern from public, anon, authenticated, service_role;

-- TM-22: no role may rewrite history.
revoke update, delete, truncate on postern.audit_log
  from public, anon, authenticated, service_role, postern_api, postern_worker;

grant usage on schema postern to postern_api, postern_worker, authenticated;

-- ------------------------------------------------------------------- components

grant select on postern.agents, postern.runs, postern.steps, postern.connections,
  postern.findings, postern.proposals, postern.notifications, postern.ledger,
  postern.audit_log
  to postern_api, postern_worker;

create policy components_read_agents on postern.agents
  for select to postern_api, postern_worker using (true);
create policy components_read_runs on postern.runs
  for select to postern_api, postern_worker using (true);
create policy components_read_steps on postern.steps
  for select to postern_api, postern_worker using (true);
create policy components_read_connections on postern.connections
  for select to postern_api, postern_worker using (true);
create policy components_read_findings on postern.findings
  for select to postern_api, postern_worker using (true);
create policy components_read_proposals on postern.proposals
  for select to postern_api, postern_worker using (true);
create policy components_read_notifications on postern.notifications
  for select to postern_api, postern_worker using (true);
create policy components_read_ledger on postern.ledger
  for select to postern_api, postern_worker using (true);
create policy components_read_audit_log on postern.audit_log
  for select to postern_api, postern_worker using (true);

grant execute on function postern.enqueue_run(text, text, text, uuid)
  to postern_api, postern_worker;
grant execute on function postern.transition_run(uuid, postern.run_status, text)
  to postern_worker;
grant execute on function postern.record_step(
  uuid, integer, text, text, text, text, text, text, integer
) to postern_worker;
grant execute on function postern.record_human_decision(uuid, text, uuid) to postern_api;
grant execute on function postern.write_audit(text, uuid, text, text, jsonb)
  to postern_api, postern_worker;
grant execute on function postern.verify_audit_chain() to postern_api, postern_worker;

-- ------------------------------------------------- people (Phase 8 web pages), TM-25

grant select on postern.users, postern.findings to authenticated;

create policy people_read_themselves on postern.users
  for select to authenticated using (id = (select auth.uid()));
create policy people_read_own_findings on postern.findings
  for select to authenticated using (user_id = (select auth.uid()));
