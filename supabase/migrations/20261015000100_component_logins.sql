-- Phase 3: postern-api and postern-worker connect as their own least-privilege roles
-- (ADR-011, ADR-012). This grants LOGIN only. Passwords never enter the repo or a shell:
-- `postern db-login rotate` sets each one as a SCRAM verifier and writes the connection
-- string straight into Secret Manager (docs/runbooks/runtime.md).

alter role postern_api with login connection limit 10;
alter role postern_worker with login connection limit 5;

-- A stuck query or an abandoned transaction can't hold locks for long.
alter role postern_api set statement_timeout = '15s';
alter role postern_api set idle_in_transaction_session_timeout = '30s';
alter role postern_worker set statement_timeout = '120s';
alter role postern_worker set idle_in_transaction_session_timeout = '300s';
