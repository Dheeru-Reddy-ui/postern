"""Threat-model tests: TM-17. See docs/threat-model.md."""

import json
from typing import Any

import pytest
from support import REPO_ROOT, FakeSupabase, advisor_lint, sample_advisor_lints, sample_project

from postern.integrations.supabase_mgmt.advisors import (
    INCOMPLETE_CHECK_LINTS,
    parse_advisor_response,
    security_lints,
)
from postern.integrations.supabase_mgmt.catalog import STATEMENTS
from postern.integrations.supabase_mgmt.client import ManagementClient

REF = "abcdefghijklmnopqrst"
CONTRACT = json.loads(
    (REPO_ROOT / "tests/fixtures/supabase/management-api-contract.json").read_text(encoding="utf-8")
)


def _lint(**changes: Any) -> dict[str, Any]:
    return advisor_lint("rls_disabled_in_public", "ERROR", "public", "notes") | changes


def test_advisor_shape_change_is_detected_not_ignored() -> None:
    """The adapter validates responses strictly. Unknown or missing fields raise a schema error;
    they never yield an empty list."""
    # The shape Postern relies on is the one Supabase's spec declares (contract fixture).
    spec = CONTRACT["endpoints"]["GET /v1/projects/{ref}/advisors/security"]["response"]
    assert spec["required"] == ["lints"]
    lint_spec = spec["properties"]["lints"]["items"]
    assert {"name", "title", "level", "categories"} <= set(lint_spec["required"])
    assert lint_spec["properties"]["level"]["enum"] == ["ERROR", "WARN", "INFO"]
    for name in INCOMPLETE_CHECK_LINTS - {"instance_db_down", "db_connection_failing"}:
        assert name in lint_spec["properties"]["name"]["enum"], name
    assert len(parse_advisor_response({"lints": [_lint()]})) == 1
    assert parse_advisor_response({"lints": []}) == [], "a genuinely empty answer is allowed"

    without_level = {k: v for k, v in _lint().items() if k != "level"}
    for changed in (
        {"results": [_lint()]},  # renamed envelope
        [_lint()],  # bare list
        {"lints": "none"},
        {"lints": [without_level]},
        {"lints": [_lint(level="CRITICAL")]},
        {"lints": [_lint(categories="SECURITY")]},
        {"lints": [_lint(name=None)]},
        None,
    ):
        with pytest.raises(ValueError, match="unexpected shape"):
            parse_advisor_response(changed)

    # "I couldn't check" is not "nothing found".
    with pytest.raises(ValueError, match="couldn't complete"):
        parse_advisor_response({"lints": [_lint(name="advisor_check_unavailable")]})

    # Through the adapter, a changed shape is recorded as an advisor failure, not as zero lints.
    fake = FakeSupabase(REF, sample_project(), advisors={"results": sample_advisor_lints()})
    result = security_lints(ManagementClient("token", fake.client(), STATEMENTS), REF)
    assert result["advisor_error"] == "ValueError"
    assert result["lints"], "Postern's own checks still ran"


def test_advisor_failure_falls_back_to_catalog_sql() -> None:
    """On adapter failure, Postern falls back to its own catalog checks through the read-only
    query endpoint (Postern's own SQL, since splinter has no license, ADR-012)."""
    expected = {
        ("rls_disabled_in_public", "notes"),
        ("rls_policy_always_true", "profiles"),
        ("security_definer_view", "order_summary"),
    }
    for failure in (500, 503, 404):
        fake = FakeSupabase(REF, sample_project(), advisors=failure)
        result = security_lints(ManagementClient("token", fake.client(), STATEMENTS), REF)
        assert result["advisor_error"] == "ManagementError"
        assert result["introspection_error"] is None
        reported = {(lint["name"], lint["metadata"]["name"]) for lint in result["lints"]}
        assert reported == expected
        assert {lint["source"] for lint in result["lints"]} == {"introspection"}
        queries = [
            json.loads(r.content)["query"]
            for r in fake.requests
            if r.url.path.endswith("read-only")
        ]
        assert sorted(queries) == sorted(STATEMENTS.values())

    # With the advisor working, both sources merge, and a lint both report is one lint.
    fake = FakeSupabase(REF, sample_project(), advisors={"lints": sample_advisor_lints()})
    result = security_lints(ManagementClient("token", fake.client(), STATEMENTS), REF)
    by_key = {(lint["name"], lint["metadata"]["name"]): lint["source"] for lint in result["lints"]}
    assert by_key == {
        ("rls_disabled_in_public", "notes"): "advisor",
        ("security_definer_view", "order_summary"): "advisor",
        ("rls_enabled_no_policy", "audit_trail"): "advisor",
        ("rls_policy_always_true", "profiles"): "introspection",
    }
