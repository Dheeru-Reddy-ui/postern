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
