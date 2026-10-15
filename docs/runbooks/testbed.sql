-- Postern testbed (ADR-008). Dheeru runs this once in the testbed project's SQL editor.
-- It is never run against postern-control or anyone else's project.
--
-- Three planted issues Security Sentinel must find, and four near-misses it must stay quiet
-- about. tests/support.py::sample_project mirrors this file; keep them in step.
-- Every row is made up. Nothing here is real data.

-- ---------------------------------------------------------------- planted issues

-- 1. RLS turned off on a table the API exposes: anyone with the public key reads everything.
create table public.notes (
  id bigint generated always as identity primary key,
  body text not null
);
alter table public.notes disable row level security;
grant select on public.notes to anon, authenticated;
insert into public.notes (body)
select 'planted note ' || n from generate_series(1, 37) as n;

-- 2. The "Enable all access" policy: RLS is on, but a USING (true) policy lets anyone read
--    and write every row.
create table public.profiles (
  id bigint generated always as identity primary key,
  display_name text not null
);
alter table public.profiles enable row level security;
create policy "Enable all access" on public.profiles
  for all to anon using (true) with check (true);
grant select, insert, update, delete on public.profiles to anon, authenticated;
insert into public.profiles (display_name)
select 'planted person ' || n from generate_series(1, 12) as n;

-- 3. A view anyone can read. Views run with their owner's rights by default, so this one
--    ignores the RLS on the table underneath.
create table public.orders (
  id bigint generated always as identity primary key,
  total_cents integer not null
);
alter table public.orders enable row level security;  -- no policy: closed on its own
insert into public.orders (total_cents)
select n * 100 from generate_series(1, 5) as n;
create view public.order_summary as select id, total_cents from public.orders;
grant select on public.order_summary to anon, authenticated;

-- ---------------------------------------------------------------- near-misses

-- 4. Owner-scoped RLS: each signed-in person sees only their own rows.
create table public.todos (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid(),
  title text not null
);
alter table public.todos enable row level security;
create policy "owners read their todos" on public.todos
  for select to authenticated using ((select auth.uid()) = user_id);
grant select on public.todos to anon, authenticated;

-- 5. Public reference data, readable by anyone on purpose, and read-only.
create table public.countries (
  code text primary key,
  name text not null
);
alter table public.countries enable row level security;
create policy "public reference data" on public.countries
  for select to anon, authenticated using (true);
grant select on public.countries to anon, authenticated;
insert into public.countries (code, name) values
  ('IN', 'India'), ('US', 'United States'), ('DE', 'Germany'), ('JP', 'Japan');

-- 6. RLS on and no policy: a grant that looks risky, but nobody can read a row.
create table public.audit_trail (
  id bigint generated always as identity primary key,
  event text not null
);
alter table public.audit_trail enable row level security;
grant select on public.audit_trail to anon, authenticated;
insert into public.audit_trail (event) values ('made-up event');

-- 7. RLS off, but in a schema the API doesn't expose.
create schema private;
revoke all on schema private from anon, authenticated;
create table private.staff_notes (
  id bigint generated always as identity primary key,
  body text not null
);
insert into private.staff_notes (body) values ('made-up staff note');

-- ---------------------------------------------------------------- check

-- Both rows must show rls_on = false. If the SQL editor offered to enable RLS and that was
-- accepted, the planted issue is gone: run the two `disable row level security` lines
-- for notes and staff_notes again (this happened on 2026-10-15).
select c.relnamespace::regnamespace as schema, c.relname as table_name,
       c.relrowsecurity as rls_on
from pg_catalog.pg_class c
where c.oid in ('public.notes'::regclass, 'private.staff_notes'::regclass);
