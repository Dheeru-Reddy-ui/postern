-- Phase 1: enable queues (pgmq) and schedules (pg_cron) on Postern's own control plane.
-- Mirrors supabase/schemas/00_extensions.sql.

create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;

create extension if not exists pgmq;
