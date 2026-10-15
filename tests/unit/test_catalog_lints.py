"""Postern's own catalog checks: each rule, with the near-misses that must stay quiet."""

from typing import Any

import pytest

from postern.integrations.supabase_mgmt.catalog import derive_lints


def relation(name: str, **changes: Any) -> dict[str, Any]:
    return {
        "schema": "public",
        "name": name,
        "kind": "r",
        "rls_enabled": True,
        "security_invoker": False,
    } | changes


def policy(name: str, cmd: str, qual: str | None, **changes: Any) -> dict[str, Any]:
    return {
        "schema": "public",
        "name": name,
        "policy": f"{name} policy",
        "permissive": "PERMISSIVE",
        "roles": ["anon"],
        "cmd": cmd,
        "qual": qual,
        "with_check": None,
    } | changes


def grant(name: str, role: str = "anon") -> dict[str, Any]:
    return {"schema": "public", "name": name, "grantee": role, "privilege_type": "SELECT"}


def lints(
    relations: list[dict[str, Any]],
    policies: list[dict[str, Any]] = (),  # type: ignore[assignment]
    grants: list[dict[str, Any]] = (),  # type: ignore[assignment]
    exposed: tuple[str, ...] = ("public",),
) -> set[tuple[str, str]]:
    found = derive_lints(exposed, relations, policies, grants)
    return {(lint["name"], lint["metadata"]["name"]) for lint in found}


def test_rls_disabled_is_flagged_only_in_exposed_schemas() -> None:
    assert lints([relation("notes", rls_enabled=False)]) == {("rls_disabled_in_public", "notes")}
    hidden = relation("notes", schema="private", rls_enabled=False)
    assert lints([hidden]) == set()
    assert lints([relation("notes")]) == set(), "RLS on with no policy: nobody can read it"


def test_policy_without_rls_is_flagged() -> None:
    found = lints(
        [relation("notes", rls_enabled=False)], [policy("notes", "SELECT", "(auth.uid() = id)")]
    )
    assert ("policy_exists_rls_disabled", "notes") in found


@pytest.mark.parametrize(
    ("cmd", "qual", "with_check", "flagged"),
    [
        ("ALL", "true", "true", True),  # "Enable all access"
        ("UPDATE", "true", None, True),
        ("DELETE", "true", None, True),
        ("INSERT", None, "true", True),
        ("SELECT", "true", None, False),  # publishing a table on purpose
        ("ALL", "(auth.uid() = owner_id)", "(auth.uid() = owner_id)", False),
    ],
)
def test_always_true_policy_is_flagged_only_when_it_allows_writes(
    cmd: str, qual: str | None, with_check: str | None, flagged: bool
) -> None:
    found = lints([relation("t")], [policy("t", cmd, qual, with_check=with_check)])
    assert (("rls_policy_always_true", "t") in found) is flagged


def test_restrictive_or_private_role_policies_are_not_flagged() -> None:
    restrictive = policy("t", "ALL", "true", permissive="RESTRICTIVE")
    service_only = policy("t", "ALL", "true", roles=["service_role"])
    assert lints([relation("t")], [restrictive, service_only]) == set()


def test_security_definer_view_is_flagged_only_when_the_api_can_read_it() -> None:
    view = relation("summary", kind="v")
    assert lints([view], grants=[grant("summary")]) == {("security_definer_view", "summary")}
    assert lints([view]) == set(), "no API role can read it"
    invoker = relation("summary", kind="v", security_invoker=True)
    assert lints([invoker], grants=[grant("summary")]) == set()
