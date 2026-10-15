"""postern-worker (Cloud Run Job): drain the tier 0 queue, then ship warehouse events.

Started by Cloud Scheduler after pg_cron has enqueued the nightly runs (ADR-012). Each run is
claimed, executed by the runtime, and finished; a run whose previous worker died is resumed
from its checkpoints. Before working, the worker raises an alert for every connection that
hasn't been checked recently, so Postern can't stop watching silently (TM-27).
"""

import logging
import os
from typing import Any

import httpx
import psycopg

from postern.config import load_config, require_env
from postern.core.redaction import configure_logging
from postern.core.runtime import Agent, Runtime
from postern.core.store import Store

MAX_RUNS_PER_EXECUTION = 200
log = logging.getLogger("postern.worker")


def alert_stale_connections(store: Store, hours: int) -> int:
    stale = store.stale_connections(hours)
    store.commit()
    for connection_id, project_ref in stale:
        log.critical(
            "connection %s (%s) hasn't been checked in %d hours",
            connection_id,
            project_ref,
            hours,
            extra={"postern_alert": "missed_nightly_run"},
        )
    return len(stale)


def drain(
    store: Store,
    runtime: Runtime,
    agents: dict[str, Agent],
    visibility: int,
    max_attempts: int,
    queue: str = "tier0",
) -> int:
    processed = 0
    while processed < MAX_RUNS_PER_EXECUTION:
        claimed = store.claim_next_run(queue, visibility, max_attempts)
        store.commit()
        if claimed is None:
            break
        run = store.load_run(claimed.run_id)
        agent = agents.get(run.agent)
        if agent is None:
            store.finish_run(claimed, "failed", "agent_not_implemented")
            store.commit()
            log.error(
                "no code for registered agent %s",
                run.agent,
                extra={"run_id": claimed.run_id, "postern_alert": "run_failed"},
            )
            continue
        try:
            outcome = runtime.execute(claimed, agent)
        except Exception:
            # Leave the message: it becomes visible again and the run resumes from its
            # checkpoints, up to the attempt limit.
            store.rollback()
            log.exception("run crashed", extra={"run_id": claimed.run_id, "agent": run.agent})
            continue
        extra: dict[str, Any] = {"run_id": claimed.run_id, "agent": run.agent}
        if outcome.status == "failed":
            extra["postern_alert"] = "run_failed"  # a failed run is a check that didn't happen
        log.log(
            logging.ERROR if outcome.status == "failed" else logging.INFO,
            "run finished: %s%s (%d steps, %d resumed)",
            outcome.status,
            f" [{outcome.error_code}]" if outcome.error_code else "",
            outcome.steps_executed,
            outcome.steps_resumed,
            extra=extra,
        )
        processed += 1
    return processed


def main() -> int:
    configure_logging("worker")
    from postern.agents.security_sentinel import DrillEcho, SecuritySentinel
    from postern.agents.toolkit import build_toolkit
    from postern.integrations.probe import ExposureProbe
    from postern.integrations.supabase_mgmt.broker import BrokerClient, google_identity_token
    from postern.llm.gateway import LLMGateway

    config = load_config()
    db_url = require_env("POSTERN_DATABASE_URL")
    api_url = require_env("POSTERN_API_URL")
    project = require_env("POSTERN_GCP_PROJECT")

    connect_args: dict[str, Any] = {"prepare_threshold": None}
    with (
        psycopg.connect(db_url, **connect_args) as conn,
        psycopg.connect(db_url, autocommit=True, **connect_args) as llm_conn,
        httpx.Client(timeout=30) as http,
    ):
        store = Store(conn)
        alert_stale_connections(store, config.worker.stale_after_hours)
        broker = BrokerClient(api_url, http, google_identity_token)
        toolkit = build_toolkit(
            lambda connection_id: broker.access_token(connection_id).access_token,
            http,
            ExposureProbe(http, config.probe),
        )
        runtime = Runtime(
            store,
            LLMGateway(llm_conn, config.llm, project),
            toolkit,
            config.agent_limits,
            config.worker.visibility_timeout_seconds,
        )
        agents: dict[str, Agent] = {
            "security_sentinel": SecuritySentinel(config.probe),
            "drill_echo": DrillEcho(),
        }
        processed = drain(
            store,
            runtime,
            agents,
            config.worker.visibility_timeout_seconds,
            config.worker.max_attempts,
        )
        log.info("worker drained the tier0 queue: %d runs", processed)

        dataset = os.environ.get("POSTERN_BQ_DATASET")
        if dataset:
            from postern.integrations.bigquery.shipper import ship_outbox, storage_write_append

            append = storage_write_append(project, dataset)
            total = 0
            while shipped := ship_outbox(conn, append):
                total += shipped
            log.info("shipped %d warehouse events to BigQuery", total)
    return 0
