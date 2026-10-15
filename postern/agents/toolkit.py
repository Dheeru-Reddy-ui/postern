"""Tool implementations for Tier 0 agents.

Every tool reads its target project from the run's context, never from its arguments
(TM-04), and returns only schema metadata, statuses and counts, never row values (invariant
4, ADR-004). The policy gate has already checked the grant before a tool runs.
"""

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from postern.core.redaction import find_secrets
from postern.core.runtime import RunAbort, RunContext, Tool
from postern.core.store import Store
from postern.integrations.probe import ExposureProbe
from postern.integrations.supabase_mgmt.advisors import security_lints
from postern.integrations.supabase_mgmt.broker import ConnectionUnavailable
from postern.integrations.supabase_mgmt.catalog import STATEMENTS
from postern.integrations.supabase_mgmt.client import ManagementClient

TokenProvider = Callable[[str], str]  # connection_id -> current access token
MAX_EXPLANATION_CHARS = 1200

_FINDING_FIELDS = {
    "fingerprint",
    "finding_type",
    "category",
    "severity",
    "source",
    "object_schema",
    "object_name",
    "title",
    "probe_http_status",
    "probe_row_count",
    "probe_confirmed",
}


def _require_connection(ctx: RunContext) -> tuple[str, str]:
    if ctx.connection is None or ctx.connection.status != "active":
        raise PermissionError("this run has no active connection")
    return ctx.connection.connection_id, ctx.connection.project_ref


def build_toolkit(
    token_for: TokenProvider,
    http: httpx.Client,
    probe: ExposureProbe,
) -> dict[str, Tool]:
    def management(ctx: RunContext) -> tuple[ManagementClient, str]:
        connection_id, ref = _require_connection(ctx)
        try:
            token = token_for(connection_id)
        except ConnectionUnavailable as error:
            raise RunAbort(f"connection_unavailable:{error}") from None
        return ManagementClient(token, http, STATEMENTS), ref

    def project_read(ctx: RunContext, args: Mapping[str, Any], store: Store) -> Any:
        client, ref = management(ctx)
        project = client.project(ref)
        return {"status": project.get("status"), "region": project.get("region")}

    def advisors_security(ctx: RunContext, args: Mapping[str, Any], store: Store) -> Any:
        client, ref = management(ctx)
        return security_lints(client, ref)

    def probe_exposure(ctx: RunContext, args: Mapping[str, Any], store: Store) -> Any:
        return probe.probe(ctx.connection, str(args["schema"]), str(args["table"])).as_output()

    def findings_record(ctx: RunContext, args: Mapping[str, Any], store: Store) -> Any:
        finding = {key: args[key] for key in _FINDING_FIELDS if key in args}
        finding_id = store.record_finding(ctx.run_id, finding)
        return {"finding_id": finding_id, "fingerprint": finding["fingerprint"]}

    def findings_explain(ctx: RunContext, args: Mapping[str, Any], store: Store) -> Any:
        explained = 0
        for item in args.get("explanations", []):
            text = str(item["text"]).strip()[:MAX_EXPLANATION_CHARS]
            if not text or find_secrets(text):
                continue
            store.set_finding_explanation(
                ctx.run_id, str(item["finding_id"]), text, str(args.get("model", "unknown"))
            )
            explained += 1
        return {"explained": explained}

    return {
        "supabase.project.read": project_read,
        "supabase.advisors.security": advisors_security,
        "probe.exposure": probe_exposure,
        "findings.record": findings_record,
        "findings.explain": findings_explain,
    }
