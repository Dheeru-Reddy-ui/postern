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
-- record_step's grant lives in 50_postern_runtime.sql (its signature changed in Phase 3).
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
