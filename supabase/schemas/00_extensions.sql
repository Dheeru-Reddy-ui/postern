-- Postern's control plane: queues (pgmq) and schedules (pg_cron). Phase 1.
-- Declarative source of truth; supabase/migrations/20261013000000_enable_pgmq_and_pg_cron.sql
-- applies the same statements to the hosted project.

-- Supabase Cron, as Supabase documents it (supabase.com/docs/guides/cron/install).
create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;

-- Supabase Queues. Not exposed through the Data API: only Postgres clients can reach it,
-- and Postern never turns on "Expose Queues via PostgREST".
create extension if not exists pgmq;
