-- Phase 2: least-privilege Postgres roles for Postern's own components (ADR-011).
-- Roles are cluster-level, so they live in a migration rather than supabase/schemas/.
-- They start NOLOGIN. Phase 3 enables login with passwords that never enter the repo; the
-- connection strings go straight into Secret Manager (control-plane-db-url-*).

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'postern_api') then
    create role postern_api nologin noinherit;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'postern_worker') then
    create role postern_worker nologin noinherit;
  end if;
end
$$;

comment on role postern_api is 'postern-api: webhooks, OAuth callbacks, human decisions.';
comment on role postern_worker is 'postern-worker: runs registered agents.';

-- The operator (postgres) may SET ROLE to each component, to check exactly what that
-- component can do (tests, SQL editor), without inheriting its privileges.
grant postern_api, postern_worker to postgres with set true, inherit false;
