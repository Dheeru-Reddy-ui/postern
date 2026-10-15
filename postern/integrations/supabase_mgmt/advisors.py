"""Adapter for Supabase's experimental advisor endpoints (TM-17).

The response is validated strictly. A shape Postern doesn't recognize is an error, never an
empty list, so a silent API change can't turn into "no problems found" (TM-16). Postern's own
catalog checks (catalog.py) run alongside it through the read-only query endpoint, so when
the advisor fails the check still happens. If both fail, it raises, and the run fails closed.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from postern.integrations.supabase_mgmt.catalog import derive_lints
from postern.integrations.supabase_mgmt.client import ManagementClient, ManagementError


class AdvisorsUnavailable(Exception):
    """Neither the advisor endpoint nor the catalog fallback produced a trustworthy result."""


# Lint names (from the Management API spec, 2026-10-14) meaning the advisor couldn't check the
# database. A response containing one is incomplete, so it is never read as "clean" (TM-16).
INCOMPLETE_CHECK_LINTS = frozenset(
    {
        "advisor_check_unavailable",
        "db_not_reachable",
        "db_connection_failing",
        "instance_db_down",
        "project_not_active",
    }
)


class _Lint(BaseModel):
    # Unknown extra keys are allowed (additions are harmless); missing or retyped required
    # keys are not.
    model_config = ConfigDict(extra="allow")
    name: str
    title: str
    level: Literal["ERROR", "WARN", "INFO"]
    categories: list[str]
    metadata: dict[str, Any] | None = None


class _AdvisorResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    lints: list[_Lint]


def _normalize(lint: _Lint) -> dict[str, Any]:
    metadata = lint.metadata or {}
    return {
        "name": lint.name,
        "title": lint.title,
        "level": lint.level,
        "categories": lint.categories,
        "metadata": {
            "schema": metadata.get("schema"),
            "name": metadata.get("name"),
            "type": metadata.get("type"),
        },
    }


def parse_advisor_response(payload: Any) -> list[dict[str, Any]]:
    """Validate an advisor response; raises ValueError on any shape it doesn't recognize."""
    try:
        parsed = _AdvisorResponse.model_validate(payload)
    except ValidationError as error:
        raise ValueError(
            f"advisor response has an unexpected shape: {error.error_count()} problems"
        ) from error
    incomplete = sorted({lint.name for lint in parsed.lints} & INCOMPLETE_CHECK_LINTS)
    if incomplete:
        raise ValueError(f"advisor couldn't complete its checks: {incomplete}")
    return [_normalize(lint) for lint in parsed.lints]


def catalog_lints(client: ManagementClient, ref: str) -> list[dict[str, Any]]:
    exposed = [row["schema"] for row in client.read_only_query(ref, "exposed_schemas")]
    return derive_lints(
        exposed,
        client.read_only_query(ref, "relations"),
        client.read_only_query(ref, "policies"),
        client.read_only_query(ref, "api_role_grants"),
    )


def _key(lint: dict[str, Any]) -> tuple[str, str, str]:
    meta = lint.get("metadata") or {}
    return (str(lint["name"]), str(meta.get("schema") or ""), str(meta.get("name") or ""))


def security_lints(client: ManagementClient, ref: str) -> dict[str, Any]:
    """Supabase's advisor lints merged with Postern's own catalog lints.

    Returns {"lints": [...], "advisor_error": str | None, "introspection_error": str | None}.
    Each lint carries "source": "advisor" or "introspection". If one source fails, its error
    is recorded and the other is used (TM-17); if both fail, AdvisorsUnavailable is raised,
    so the run fails closed instead of reporting nothing found (TM-16).
    """
    advisor: list[dict[str, Any]] = []
    catalog: list[dict[str, Any]] = []
    advisor_error: str | None = None
    introspection_error: str | None = None
    try:
        advisor = parse_advisor_response(client.security_advisors(ref))
    except (ManagementError, ValueError) as error:
        advisor_error = type(error).__name__
    try:
        catalog = catalog_lints(client, ref)
    except Exception as error:  # any failure here means the check is incomplete
        introspection_error = type(error).__name__
    if advisor_error and introspection_error:
        raise AdvisorsUnavailable(
            f"advisors failed ({advisor_error}) and the catalog checks failed"
            f" ({introspection_error})"
        )
    merged = {_key(lint): lint | {"source": "advisor"} for lint in advisor}
    for lint in catalog:
        merged.setdefault(_key(lint), lint | {"source": "introspection"})
    return {
        "lints": [merged[key] for key in sorted(merged)],
        "advisor_error": advisor_error,
        "introspection_error": introspection_error,
    }
