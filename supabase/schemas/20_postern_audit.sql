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
