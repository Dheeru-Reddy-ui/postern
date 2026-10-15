-- Phase 3: runtime support (ADR-012). Every function here is the only write path for its
-- data, audits in the same transaction (invariant 8), and emits warehouse events through
-- postern.emit_event (ADR-003).

-- ---------------------------------------------------------------------------- tables

-- Every LLM call, through postern/llm/gateway.py only (invariant 6). A call is reserved,
-- with its worst-case cost, before it is made, so the monthly cap holds even when calls
-- run concurrently (TM-11).
create table postern.llm_calls (
  id uuid primary key default gen_random_uuid(),
  run_id uuid references postern.runs (id) on delete cascade,
  step_index integer,
  agent text not null,
  user_id uuid references postern.users (id) on delete cascade,
  connection_id uuid references postern.connections (id) on delete cascade,
  purpose text not null,
  provider text not null,
  model text not null,
  is_fallback boolean not null default false,
  prompt jsonb not null,
  response jsonb,
  tool_calls jsonb,
  input_tokens integer check (input_tokens >= 0),
  output_tokens integer check (output_tokens >= 0),
  estimated_cost_usd numeric(12, 6) not null check (estimated_cost_usd >= 0),
  cost_usd numeric(12, 6) check (cost_usd >= 0),
  latency_ms integer check (latency_ms >= 0),
  status text not null check (status in ('reserved', 'ok', 'error', 'refused_budget')),
  error_code text,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);
comment on table postern.llm_calls is
  'Every LLM call with its prompt, response, tokens and cost (invariant 6). Never row data.';
create index llm_calls_created on postern.llm_calls (created_at);
create index llm_calls_run on postern.llm_calls (run_id);

-- A single-use link the operator issues so a person can connect a project (until the web
-- onboarding of Phase 8 replaces it). Only the token's SHA-256 is stored.
create table postern.connect_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references postern.users (id) on delete cascade,
  project_ref text not null check (project_ref ~ '^[a-z]{20}$'),
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);
comment on table postern.connect_requests is 'Single-use connect links (hashes only).';

-- OAuth state for one authorization attempt: single use, short-lived (TM-20).
create table postern.oauth_states (
  state_hash text primary key check (state_hash ~ '^[0-9a-f]{64}$'),
  connect_request_id uuid not null references postern.connect_requests (id) on delete cascade,
  code_verifier text not null check (length(code_verifier) between 43 and 128),
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);
comment on table postern.oauth_states is 'PKCE verifier and state for one OAuth attempt.';

-- Transactional outbox for the warehouse (ADR-003). Rows are written with the state change
-- they describe and shipped to BigQuery afterwards.
create table postern.warehouse_outbox (
  event_id uuid primary key default gen_random_uuid(),
  table_name text not null check (
    table_name in ('runs', 'steps', 'llm_calls', 'findings', 'ledger', 'notifications',
                   'eval_results')
  ),
  event_ts timestamptz not null default now(),
  row jsonb not null,
  created_at timestamptz not null default now(),
  shipped_at timestamptz
);
comment on table postern.warehouse_outbox is 'Events waiting to be shipped to BigQuery.';
create index warehouse_outbox_unshipped
  on postern.warehouse_outbox (created_at) where shipped_at is null;

alter table postern.llm_calls enable row level security;
alter table postern.connect_requests enable row level security;
alter table postern.oauth_states enable row level security;
alter table postern.warehouse_outbox enable row level security;

-- -------------------------------------------------------------------------- events

create function postern.emit_event(p_table text, p_row jsonb)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  new_event uuid := gen_random_uuid();
begin
  insert into postern.warehouse_outbox (event_id, table_name, event_ts, row)
  values (new_event, p_table, now(),
          p_row || jsonb_build_object('event_id', new_event, 'event_ts', now()));
  return new_event;
end
$$;

create function postern.claim_outbox(p_limit integer)
returns setof postern.warehouse_outbox
language sql
security definer
set search_path = ''
as $$
  select * from postern.warehouse_outbox o
  where o.shipped_at is null
  order by o.created_at
  limit p_limit
  for update skip locked
$$;

create function postern.mark_outbox_shipped(p_event_ids uuid[])
returns integer
language sql
security definer
set search_path = ''
as $$
  with shipped as (
    update postern.warehouse_outbox o set shipped_at = now()
    where o.event_id = any (p_event_ids) and o.shipped_at is null
    returning 1
  )
  select count(*)::integer from shipped
$$;

-- -------------------------------------------------------------------- worker queue

-- Claim the next run from a tier queue. Re-checks registration at dequeue (TM-23), resumes a
-- run whose previous worker died (TM-18), and gives up after p_max_attempts deliveries.
create function postern.claim_next_run(
  p_queue text,
  p_visibility_seconds integer,
  p_max_attempts integer default 5
)
returns table (claimed_run_id uuid, claimed_msg_id bigint, read_count integer, resumed boolean)
language plpgsql
security definer
set search_path = ''
as $$
declare
  message record;
  run_row postern.runs;
  agent_status postern.agent_status;
begin
  if p_queue not in ('tier0', 'tier1', 'tier2', 'meta') then
    raise exception 'unknown queue %', p_queue using errcode = 'check_violation';
  end if;

  loop
    select m.msg_id, m.read_ct, m.message
      into message
      from pgmq.read(p_queue, p_visibility_seconds, 1) m;
    if not found then
      return;
    end if;

    select * into run_row from postern.runs r
     where r.id = (message.message ->> 'run_id')::uuid
     for update;
    if not found then
      perform pgmq.archive(p_queue, message.msg_id);
      perform postern.write_audit('queue.discarded', null, 'queue', p_queue,
        jsonb_build_object('msg_id', message.msg_id, 'reason', 'run_not_found'));
      continue;
    end if;

    if run_row.status in ('completed', 'failed', 'rolled_back', 'awaiting_human') then
      perform pgmq.archive(p_queue, message.msg_id);
      continue;
    end if;

    select a.status into agent_status from postern.agents a where a.name = run_row.agent;
    if agent_status is distinct from 'active' then
      perform postern.write_audit('policy.refused', run_row.id, 'agent', run_row.agent,
        jsonb_build_object('reason', 'agent not active at dequeue', 'status', agent_status));
      perform postern.transition_run(run_row.id, 'failed', 'agent_not_registered');
      perform pgmq.archive(p_queue, message.msg_id);
      continue;
    end if;

    if message.read_ct > p_max_attempts then
      perform postern.transition_run(run_row.id, 'failed', 'too_many_attempts');
      perform pgmq.archive(p_queue, message.msg_id);
      continue;
    end if;

    if run_row.status = 'queued' then
      perform postern.transition_run(run_row.id, 'running');
      return query select run_row.id, message.msg_id, message.read_ct::integer, false;
    else
      perform postern.write_audit('run.resumed', run_row.id, 'run', run_row.id::text,
        jsonb_build_object('msg_id', message.msg_id, 'read_count', message.read_ct));
      return query select run_row.id, message.msg_id, message.read_ct::integer, true;
    end if;
    return;
  end loop;
end
$$;

-- Keep a long run's message invisible to other workers (heartbeat).
create function postern.extend_claim(p_queue text, p_msg_id bigint, p_visibility_seconds integer)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform pgmq.set_vt(p_queue, p_msg_id, p_visibility_seconds);
end
$$;

-- Finish a claimed run and remove its message, in one transaction.
create function postern.finish_run(
  p_run_id uuid,
  p_queue text,
  p_msg_id bigint,
  p_to postern.run_status,
  p_error_code text default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_to not in ('completed', 'failed', 'awaiting_human') then
    raise exception 'a worker can only finish a run as completed, failed or awaiting_human'
      using errcode = 'check_violation';
  end if;
  perform postern.transition_run(p_run_id, p_to, p_error_code);
  perform pgmq.archive(p_queue, p_msg_id);
end
$$;

-- ------------------------------------------------------------------------ findings

-- Record what a run found. The connection and owner come from the run, never from the
-- caller (TM-04). Idempotent per (connection, fingerprint), so a resumed run can't
-- duplicate a finding (TM-18).
create function postern.record_finding(
  p_run_id uuid,
  p_fingerprint text,
  p_finding_type text,
  p_category text,
  p_severity text,
  p_source text,
  p_object_schema text,
  p_object_name text,
  p_title text,
  p_probe_http_status integer,
  p_probe_row_count integer,
  p_probe_confirmed boolean
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  run_row postern.runs;
  existing postern.findings;
  finding_id uuid;
  outcome text;
begin
  select * into run_row from postern.runs r where r.id = p_run_id;
  if not found or run_row.status <> 'running' then
    raise exception 'run % is not running', p_run_id using errcode = 'check_violation';
  end if;
  if run_row.connection_id is null then
    raise exception 'run % has no connection', p_run_id using errcode = 'check_violation';
  end if;

  select * into existing from postern.findings f
   where f.connection_id = run_row.connection_id and f.fingerprint = p_fingerprint
   for update;

  if not found then
    insert into postern.findings (connection_id, user_id, run_id, agent, fingerprint,
      finding_type, category, severity, source, object_schema, object_name, title,
      probe_http_status, probe_row_count, probe_confirmed, last_seen_run_id)
    values (run_row.connection_id, run_row.user_id, p_run_id, run_row.agent, p_fingerprint,
      p_finding_type, p_category, p_severity, p_source, p_object_schema, p_object_name,
      p_title, p_probe_http_status, p_probe_row_count, p_probe_confirmed, p_run_id)
    returning id into finding_id;
    outcome := 'found';
  elsif existing.last_seen_run_id = p_run_id then
    return existing.id;
  else
    outcome := case
      when existing.status in ('fixed', 'verified_closed') then 'reopened'
      else 'seen_again'
    end;
    update postern.findings f
       set severity = p_severity, source = p_source, title = p_title,
           probe_http_status = p_probe_http_status, probe_row_count = p_probe_row_count,
           probe_confirmed = p_probe_confirmed, last_seen_at = now(),
           last_seen_run_id = p_run_id, updated_at = now(),
           status = case when outcome = 'reopened' then 'reopened' else f.status end
     where f.id = existing.id
    returning f.id into finding_id;
  end if;

  perform postern.write_audit(
    'finding.' || outcome, p_run_id, 'finding', finding_id::text,
    jsonb_build_object('fingerprint', p_fingerprint, 'finding_type', p_finding_type,
      'severity', p_severity, 'source', p_source, 'probe_confirmed', p_probe_confirmed,
      'probe_row_count', p_probe_row_count)
  );
  perform postern.emit_event('findings', jsonb_build_object(
    'finding_id', finding_id, 'fingerprint', p_fingerprint, 'run_id', p_run_id,
    'agent', run_row.agent, 'finding_type', p_finding_type,
    'category', p_category, 'severity', p_severity, 'source', p_source,
    'object_schema', p_object_schema, 'object_name', p_object_name,
    'probe_http_status', p_probe_http_status, 'probe_row_count', p_probe_row_count,
    'probe_confirmed', p_probe_confirmed,
    'status', case when outcome = 'reopened' then 'reopened' else coalesce(existing.status, 'open') end,
    'user_id', run_row.user_id, 'connection_id', run_row.connection_id
  ));
  return finding_id;
end
$$;

-- The LLM can add words to a finding; it can't change what was found (TM-16).
create function postern.set_finding_explanation(
  p_run_id uuid,
  p_finding_id uuid,
  p_explanation text,
  p_model text
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  run_row postern.runs;
begin
  select * into run_row from postern.runs r where r.id = p_run_id;
  if not found or run_row.status <> 'running' then
    raise exception 'run % is not running', p_run_id using errcode = 'check_violation';
  end if;
  update postern.findings f
     set explanation = p_explanation, explanation_model = p_model, updated_at = now()
   where f.id = p_finding_id and f.connection_id = run_row.connection_id;
  if not found then
    raise exception 'finding % does not belong to run %''s connection', p_finding_id, p_run_id
      using errcode = 'insufficient_privilege';
  end if;
  perform postern.write_audit('finding.explained', p_run_id, 'finding', p_finding_id::text,
    jsonb_build_object('model', p_model, 'length', length(p_explanation)));
end
$$;

-- ----------------------------------------------------------------------- LLM spend

create function postern.llm_spend_this_month()
returns numeric
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(sum(coalesce(c.cost_usd, c.estimated_cost_usd)), 0)
  from postern.llm_calls c
  where c.created_at >= date_trunc('month', now() at time zone 'UTC') at time zone 'UTC'
    and c.status in ('reserved', 'ok', 'error')
$$;

-- Reserve a call's worst-case cost under the monthly cap (TM-11). Returns the call's id, or
-- null when the cap would be exceeded; the refusal itself is recorded and audited.
create function postern.reserve_llm_call(
  p_run_id uuid,
  p_step_index integer,
  p_agent text,
  p_purpose text,
  p_provider text,
  p_model text,
  p_is_fallback boolean,
  p_prompt jsonb,
  p_estimated_cost_usd numeric,
  p_monthly_cap_usd numeric
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  run_row postern.runs;
  spent numeric;
  call_id uuid;
begin
  perform pg_advisory_xact_lock(7364726532);
  select * into run_row from postern.runs r where r.id = p_run_id;
  spent := postern.llm_spend_this_month();

  if spent + p_estimated_cost_usd > p_monthly_cap_usd then
    insert into postern.llm_calls (run_id, step_index, agent, user_id, connection_id, purpose,
      provider, model, is_fallback, prompt, estimated_cost_usd, status, error_code,
      completed_at)
    values (p_run_id, p_step_index, p_agent, run_row.user_id, run_row.connection_id,
      p_purpose, p_provider, p_model, p_is_fallback, p_prompt, p_estimated_cost_usd,
      'refused_budget', 'monthly_cap_reached', now())
    returning id into call_id;
    perform postern.write_audit('llm.refused_budget', p_run_id, 'llm_call', call_id::text,
      jsonb_build_object('spent_usd', spent, 'cap_usd', p_monthly_cap_usd,
        'estimated_cost_usd', p_estimated_cost_usd));
    return null;
  end if;

  insert into postern.llm_calls (run_id, step_index, agent, user_id, connection_id, purpose,
    provider, model, is_fallback, prompt, estimated_cost_usd, status)
  values (p_run_id, p_step_index, p_agent, run_row.user_id, run_row.connection_id, p_purpose,
    p_provider, p_model, p_is_fallback, p_prompt, p_estimated_cost_usd, 'reserved')
  returning id into call_id;
  return call_id;
end
$$;

create function postern.complete_llm_call(
  p_call_id uuid,
  p_status text,
  p_model text,
  p_response jsonb,
  p_tool_calls jsonb,
  p_input_tokens integer,
  p_output_tokens integer,
  p_cost_usd numeric,
  p_latency_ms integer,
  p_error_code text default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  call_row postern.llm_calls;
begin
  if p_status not in ('ok', 'error') then
    raise exception 'status must be ok or error' using errcode = 'check_violation';
  end if;
  update postern.llm_calls c
     set status = p_status, model = coalesce(p_model, c.model), response = p_response,
         tool_calls = p_tool_calls, input_tokens = p_input_tokens,
         output_tokens = p_output_tokens, cost_usd = p_cost_usd, latency_ms = p_latency_ms,
         error_code = p_error_code, completed_at = now()
   where c.id = p_call_id and c.status = 'reserved'
  returning * into call_row;
  if not found then
    raise exception 'llm call % is not reserved', p_call_id using errcode = 'check_violation';
  end if;
  perform postern.write_audit('llm.called', call_row.run_id, 'llm_call', p_call_id::text,
    jsonb_build_object('model', call_row.model, 'status', p_status, 'cost_usd', p_cost_usd,
      'input_tokens', p_input_tokens, 'output_tokens', p_output_tokens));
  perform postern.emit_event('llm_calls', jsonb_build_object(
    'call_id', call_row.id, 'run_id', call_row.run_id, 'step_index', call_row.step_index,
    'agent', call_row.agent, 'provider', call_row.provider, 'model', call_row.model,
    'is_fallback', call_row.is_fallback, 'prompt', call_row.prompt,
    'response', call_row.response, 'tool_calls', call_row.tool_calls,
    'input_tokens', call_row.input_tokens, 'output_tokens', call_row.output_tokens,
    'cost_usd', call_row.cost_usd, 'latency_ms', call_row.latency_ms,
    'status', call_row.status, 'error_code', call_row.error_code,
    'user_id', call_row.user_id, 'connection_id', call_row.connection_id
  ));
end
$$;

-- --------------------------------------------------------------------- connections

-- Operator only: issue a single-use connect link for one person and one project.
create function postern.create_connect_request(
  p_user_id uuid,
  p_project_ref text,
  p_token_hash text,
  p_ttl_minutes integer default 60
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  request_id uuid;
begin
  insert into postern.connect_requests (user_id, project_ref, token_hash, expires_at)
  values (p_user_id, p_project_ref, p_token_hash,
          now() + make_interval(mins => least(greatest(p_ttl_minutes, 5), 1440)))
  returning id into request_id;
  perform postern.write_audit('connection.link_issued', null, 'connect_request',
    request_id::text, jsonb_build_object('user_id', p_user_id, 'project_ref', p_project_ref));
  return request_id;
end
$$;

-- Consume a connect link (single use) and open one OAuth attempt (TM-20).
create function postern.start_oauth(
  p_token_hash text,
  p_state_hash text,
  p_code_verifier text
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  request_row postern.connect_requests;
begin
  update postern.connect_requests c set used_at = now()
   where c.token_hash = p_token_hash and c.used_at is null and c.expires_at > now()
  returning * into request_row;
  if not found then
    raise exception 'connect link is invalid, used or expired' using errcode = 'invalid_authorization_specification';
  end if;
  insert into postern.oauth_states (state_hash, connect_request_id, code_verifier, expires_at)
  values (p_state_hash, request_row.id, p_code_verifier, now() + interval '10 minutes');
  return request_row.project_ref;
end
$$;

-- Consume the OAuth state on callback: single use, unexpired (TM-20).
create function postern.finish_oauth(p_state_hash text)
returns table (user_id uuid, project_ref text, code_verifier text)
language plpgsql
security definer
set search_path = ''
as $$
declare
  state_row postern.oauth_states;
begin
  update postern.oauth_states s set used_at = now()
   where s.state_hash = p_state_hash and s.used_at is null and s.expires_at > now()
  returning * into state_row;
  if not found then
    raise exception 'oauth state is missing, used or expired' using errcode = 'invalid_authorization_specification';
  end if;
  return query
    select c.user_id, c.project_ref, state_row.code_verifier
      from postern.connect_requests c where c.id = state_row.connect_request_id;
end
$$;

create function postern.save_connection(
  p_user_id uuid,
  p_project_ref text,
  p_access_token_ciphertext bytea,
  p_refresh_token_ciphertext bytea,
  p_token_key_version text,
  p_token_expires_at timestamptz
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  connection_id uuid;
begin
  insert into postern.connections (user_id, kind, supabase_project_ref, status, status_reason,
    access_token_ciphertext, refresh_token_ciphertext, token_key_version, token_expires_at,
    verified_at)
  values (p_user_id, 'supabase_project', p_project_ref, 'active', null,
    p_access_token_ciphertext, p_refresh_token_ciphertext, p_token_key_version,
    p_token_expires_at, now())
  on conflict (user_id, supabase_project_ref) where kind = 'supabase_project' do update
    set status = 'active', status_reason = null,
        access_token_ciphertext = excluded.access_token_ciphertext,
        refresh_token_ciphertext = excluded.refresh_token_ciphertext,
        token_key_version = excluded.token_key_version,
        token_expires_at = excluded.token_expires_at, verified_at = now(), updated_at = now()
  returning id into connection_id;
  perform postern.write_audit('connection.connected', null, 'connection', connection_id::text,
    jsonb_build_object('user_id', p_user_id, 'project_ref', p_project_ref));
  return connection_id;
end
$$;

create function postern.store_refreshed_tokens(
  p_connection_id uuid,
  p_access_token_ciphertext bytea,
  p_refresh_token_ciphertext bytea,
  p_token_key_version text,
  p_token_expires_at timestamptz
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update postern.connections c
     set access_token_ciphertext = p_access_token_ciphertext,
         refresh_token_ciphertext = coalesce(p_refresh_token_ciphertext,
                                             c.refresh_token_ciphertext),
         token_key_version = p_token_key_version, token_expires_at = p_token_expires_at,
         updated_at = now()
   where c.id = p_connection_id and c.status = 'active';
  if not found then
    raise exception 'connection % is not active', p_connection_id using errcode = 'check_violation';
  end if;
end
$$;

-- Revoking or erroring a connection destroys its tokens immediately (TM-27, TM-30).
create function postern.set_connection_status(
  p_connection_id uuid,
  p_status text,
  p_reason text
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update postern.connections c
     set status = p_status, status_reason = p_reason, updated_at = now(),
         access_token_ciphertext = case when p_status = 'active'
                                        then c.access_token_ciphertext end,
         refresh_token_ciphertext = case when p_status = 'active'
                                         then c.refresh_token_ciphertext end
   where c.id = p_connection_id;
  if not found then
    raise exception 'connection % does not exist', p_connection_id using errcode = 'no_data_found';
  end if;
  perform postern.write_audit('connection.' || p_status, null, 'connection',
    p_connection_id::text, jsonb_build_object('reason', p_reason));
end
$$;

-- Store the owner's pasted public key. The table's check constraint rejects anything but a
-- publishable or legacy anon key, and the key itself never reaches the audit log (TM-06).
create function postern.set_publishable_key(p_connection_id uuid, p_key text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update postern.connections c set publishable_key = p_key, updated_at = now()
   where c.id = p_connection_id and c.kind = 'supabase_project';
  if not found then
    raise exception 'connection % does not exist', p_connection_id using errcode = 'no_data_found';
  end if;
  perform postern.write_audit('connection.key_set', null, 'connection', p_connection_id::text,
    jsonb_build_object('key_kind', postern.supabase_key_kind(p_key)));
end
$$;

create function postern.mark_connection_checked(p_run_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update postern.connections c set last_checked_at = now()
   where c.id = (select r.connection_id from postern.runs r where r.id = p_run_id);
end
$$;

-- Active connections whose last successful check is older than p_hours (TM-27).
create function postern.stale_connections(p_hours integer default 26)
returns table (connection_id uuid, project_ref text, last_checked_at timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
  select c.id, c.supabase_project_ref, c.last_checked_at
    from postern.connections c
   where c.status = 'active' and c.kind = 'supabase_project'
     and c.created_at < now() - make_interval(hours => p_hours)
     and (c.last_checked_at is null or c.last_checked_at < now() - make_interval(hours => p_hours))
$$;

-- ---------------------------------------------------------------- privileges, RLS

revoke all on postern.llm_calls, postern.connect_requests, postern.oauth_states,
  postern.warehouse_outbox from public, anon, authenticated, service_role;
revoke all on all functions in schema postern from public, anon, authenticated, service_role;

grant select on postern.llm_calls, postern.warehouse_outbox to postern_worker, postern_api;

create policy components_read_llm_calls on postern.llm_calls
  for select to postern_api, postern_worker using (true);
create policy components_read_outbox on postern.warehouse_outbox
  for select to postern_api, postern_worker using (true);

-- Worker: claim, run, record, finish, ship.
grant execute on function postern.claim_next_run(text, integer, integer) to postern_worker;
grant execute on function postern.extend_claim(text, bigint, integer) to postern_worker;
grant execute on function postern.finish_run(uuid, text, bigint, postern.run_status, text)
  to postern_worker;
grant execute on function postern.record_step(
  uuid, integer, text, text, text, text, text, text, integer, jsonb
) to postern_worker;
grant execute on function postern.record_finding(
  uuid, text, text, text, text, text, text, text, text, integer, integer, boolean
) to postern_worker;
grant execute on function postern.set_finding_explanation(uuid, uuid, text, text)
  to postern_worker;
grant execute on function postern.llm_spend_this_month() to postern_worker, postern_api;
grant execute on function postern.reserve_llm_call(
  uuid, integer, text, text, text, text, boolean, jsonb, numeric, numeric
) to postern_worker;
grant execute on function postern.complete_llm_call(
  uuid, text, text, jsonb, jsonb, integer, integer, numeric, integer, text
) to postern_worker;
grant execute on function postern.mark_connection_checked(uuid) to postern_worker;
grant execute on function postern.stale_connections(integer) to postern_worker;
grant execute on function postern.claim_outbox(integer) to postern_worker;
grant execute on function postern.mark_outbox_shipped(uuid[]) to postern_worker;

-- API: OAuth, tokens, connection status.
grant execute on function postern.start_oauth(text, text, text) to postern_api;
grant execute on function postern.finish_oauth(text) to postern_api;
grant execute on function postern.save_connection(uuid, text, bytea, bytea, text, timestamptz)
  to postern_api;
grant execute on function postern.store_refreshed_tokens(uuid, bytea, bytea, text, timestamptz)
  to postern_api;
grant execute on function postern.set_connection_status(uuid, text, text)
  to postern_api, postern_worker;
grant execute on function postern.set_publishable_key(uuid, text) to postern_api;

-- Re-grant Phase 2's component functions, since the revoke above covers every function.
grant execute on function postern.enqueue_run(text, text, text, uuid)
  to postern_api, postern_worker;
grant execute on function postern.transition_run(uuid, postern.run_status, text)
  to postern_worker;
grant execute on function postern.record_human_decision(uuid, text, uuid) to postern_api;
grant execute on function postern.write_audit(text, uuid, text, text, jsonb)
  to postern_api, postern_worker;
grant execute on function postern.verify_audit_chain() to postern_api, postern_worker;
