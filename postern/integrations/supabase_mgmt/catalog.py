"""The only SQL Postern ever sends to a user's project (TM-01, ADR-005).

Each statement reads Postgres's catalog through the read-only endpoint. None of them selects
from a user table, so no row values can come back. The statements are fixed constants;
`ManagementClient.read_only_query` refuses anything else.

The lints in `derive_lints` are Postern's own checks for the ways anonymous visitors reach
data. They run on every check, merged with Supabase's advisor lints, because the advisor
can't be the only source: it is experimental (TM-17), and its lint list (Management API
spec, 2026-10-14) has nothing for a `USING (true)` policy. Where an advisor lint covers the
same problem, the same lint name is used, so the two merge into one finding.
"""

from collections.abc import Iterable
from typing import Any

SYSTEM_SCHEMAS = (
    "'pg_catalog', 'information_schema', 'pg_toast', 'auth', 'storage', 'realtime',"
    " '_realtime', 'supabase_functions', 'supabase_migrations', 'extensions', 'graphql',"
    " 'graphql_public', 'pgbouncer', 'vault', 'net', 'cron', 'pgmq', 'pgmq_public',"
    " 'pgsodium', 'pgsodium_masks', '_analytics', 'pgtle', 'tiger', 'topology'"
)

STATEMENTS: dict[str, str] = {
    "exposed_schemas": (
        "select trim(s) as schema from unnest(string_to_array("
        "coalesce(nullif(current_setting('pgrst.db_schemas', true), ''), 'public'), ',')) as s"
    ),
    "relations": (
        "select n.nspname as schema, c.relname as name, c.relkind::text as kind,"
        " c.relrowsecurity as rls_enabled,"
        " coalesce('security_invoker=true' = any (c.reloptions)"
        " or 'security_invoker=on' = any (c.reloptions), false) as security_invoker"
        " from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace"
        " where c.relkind in ('r', 'p', 'v', 'm', 'f')"
        f" and n.nspname not in ({SYSTEM_SCHEMAS}) and n.nspname not like 'pg\\_%'"
        " order by 1, 2"
    ),
    "policies": (
        "select schemaname as schema, tablename as name, policyname as policy,"
        " permissive, roles::text[] as roles, cmd, qual, with_check"
        f" from pg_catalog.pg_policies where schemaname not in ({SYSTEM_SCHEMAS})"
        " order by 1, 2, 3"
    ),
    # Not information_schema.role_table_grants: it shows only grants involving the current
    # role, and the read-only endpoint runs as supabase_read_only_user, so it would always
    # be empty. has_*_privilege answers for any role.
    "api_role_grants": (
        "select n.nspname as schema, c.relname as name, r.rolname as grantee,"
        " 'SELECT' as privilege_type"
        " from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace"
        " cross join (values ('anon'), ('authenticated')) as r (rolname)"
        " where c.relkind in ('r', 'p', 'v', 'm', 'f')"
        f" and n.nspname not in ({SYSTEM_SCHEMAS}) and n.nspname not like 'pg\\_%'"
        " and pg_catalog.has_schema_privilege(r.rolname, n.oid, 'USAGE')"
        " and pg_catalog.has_table_privilege(r.rolname, c.oid, 'SELECT')"
        " order by 1, 2, 3"
    ),
}

_API_ROLES = {"anon", "authenticated", "public"}
_WRITE_COMMANDS = {"ALL", "INSERT", "UPDATE", "DELETE"}  # pg_policies.cmd


def _lint(name: str, title: str, level: str, schema: str, obj: str, kind: str) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "level": level,
        "categories": ["SECURITY"],
        "metadata": {"schema": schema, "name": obj, "type": kind},
    }


def derive_lints(
    exposed_schemas: Iterable[str],
    relations: Iterable[dict[str, Any]],
    policies: Iterable[dict[str, Any]],
    grants: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Security lints computed from catalog rows, in the advisor's lint format."""
    exposed = set(exposed_schemas)
    relations = list(relations)
    policies = list(policies)
    readable = {(g["schema"], g["name"]) for g in grants if g["privilege_type"] == "SELECT"}
    has_policy = {(p["schema"], p["name"]) for p in policies}
    lints: list[dict[str, Any]] = []
    for rel in relations:
        key = (rel["schema"], rel["name"])
        if rel["schema"] not in exposed:
            continue
        if rel["kind"] in ("r", "p") and not rel["rls_enabled"]:
            if key in has_policy:
                lints.append(
                    _lint(
                        "policy_exists_rls_disabled",
                        "Policy Exists RLS Disabled",
                        "ERROR",
                        *key,
                        "table",
                    )
                )
            lints.append(
                _lint("rls_disabled_in_public", "RLS Disabled in Public", "ERROR", *key, "table")
            )
        if rel["kind"] == "v" and not rel["security_invoker"] and key in readable:
            lints.append(
                _lint("security_definer_view", "Security Definer View", "ERROR", *key, "view")
            )
    for policy in policies:
        key = (policy["schema"], policy["name"])
        # A restrictive policy can only narrow access, so `true` there is harmless.
        if key[0] not in exposed or policy["permissive"] != "PERMISSIVE":
            continue
        roles = {str(role) for role in policy["roles"] or []}
        if not roles & _API_ROLES:
            continue
        # A read-only `true` policy is how Supabase publishes a table on purpose (ADR-008's
        # public reference table), so only policies that let anyone write are flagged: the
        # "Enable all access" mistake.
        if policy["cmd"] not in _WRITE_COMMANDS:
            continue
        if (policy["qual"] or "").strip() == "true" or (
            policy["with_check"] or ""
        ).strip() == "true":
            lints.append(
                _lint("rls_policy_always_true", "RLS Policy Always True", "WARN", *key, "table")
            )
    unique = {(x["name"], x["metadata"]["schema"], x["metadata"]["name"]): x for x in lints}
    return list(unique.values())
