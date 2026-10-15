"""Supabase Management API client: an endpoint allowlist, not a general client (ADR-005, TM-06).

Only the endpoints Postern needs exist here. Endpoints that the granted scopes would allow but
Postern must never call (SQL snippets, backups, pooler config, temporary-access role
assumption) can't be reached through this module. SQL goes only through the read-only
endpoint, and only as a statement from the catalog allowlist (TM-01).
"""

import hashlib
import re
from typing import Any

import httpx

API_BASE = "https://api.supabase.com"
PROJECT_REF = re.compile(r"^[a-z]{20}$")

ALLOWED_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/v1/projects"),
        ("GET", "/v1/projects/{ref}"),
        ("GET", "/v1/projects/{ref}/jit-access"),
        ("GET", "/v1/projects/{ref}/advisors/security"),
        ("GET", "/v1/projects/{ref}/advisors/performance"),
        ("POST", "/v1/projects/{ref}/database/query/read-only"),
        ("GET", "/v1/projects/{ref}/storage/buckets"),
    }
)


class ManagementError(Exception):
    def __init__(self, status: int, endpoint: str) -> None:
        super().__init__(f"Supabase Management API {endpoint} returned HTTP {status}")
        self.status = status
        self.endpoint = endpoint


class Unauthorized(ManagementError):
    """The token was revoked or expired (TM-27)."""


class StatementNotAllowed(PermissionError):
    pass


def statement_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


class ManagementClient:
    def __init__(
        self,
        access_token: str,
        http: httpx.Client,
        allowed_statements: dict[str, str],
        base_url: str = API_BASE,
    ) -> None:
        self._token = access_token
        self._http = http
        self._base = base_url
        # name -> SQL, fixed at construction; queries are looked up by name and hash.
        self._statements = dict(allowed_statements)
        self._statement_hashes = {statement_hash(sql) for sql in self._statements.values()}

    def _call(
        self, method: str, template: str, ref: str | None = None, body: dict[str, Any] | None = None
    ) -> Any:
        if (method, template) not in ALLOWED_ENDPOINTS:
            raise PermissionError(f"{method} {template} is not an allowed endpoint")
        path = template
        if "{ref}" in template:
            if ref is None or not PROJECT_REF.fullmatch(ref):
                raise ValueError("a valid 20-letter project ref is required")
            path = template.replace("{ref}", ref)
        response = self._http.request(
            method,
            self._base + path,
            json=body,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
        )
        if response.status_code == 401:
            raise Unauthorized(401, template)
        if response.status_code >= 400:
            raise ManagementError(response.status_code, template)
        return response.json() if response.content else None

    def list_projects(self) -> list[dict[str, Any]]:
        result = self._call("GET", "/v1/projects")
        return list(result or [])

    def project(self, ref: str) -> dict[str, Any]:
        return dict(self._call("GET", "/v1/projects/{ref}", ref) or {})

    def temporary_access_state(self, ref: str) -> str:
        """'enabled', 'disabled' or 'unavailable' (ADR-005's guard)."""
        result = self._call("GET", "/v1/projects/{ref}/jit-access", ref) or {}
        state = str(result.get("state", "unknown"))
        return state if state in {"enabled", "disabled"} else "unavailable"

    def security_advisors(self, ref: str) -> Any:
        return self._call("GET", "/v1/projects/{ref}/advisors/security", ref)

    def performance_advisors(self, ref: str) -> Any:
        return self._call("GET", "/v1/projects/{ref}/advisors/performance", ref)

    def storage_buckets(self, ref: str) -> list[dict[str, Any]]:
        return list(self._call("GET", "/v1/projects/{ref}/storage/buckets", ref) or [])

    def read_only_query(self, ref: str, statement_name: str) -> list[dict[str, Any]]:
        sql = self._statements.get(statement_name)
        if sql is None or statement_hash(sql) not in self._statement_hashes:
            raise StatementNotAllowed(f"{statement_name!r} is not an allowlisted statement")
        result = self._call(
            "POST", "/v1/projects/{ref}/database/query/read-only", ref, {"query": sql}
        )
        return list(result or [])
