"""Test doubles shared across the suite: the outside world, faked at its boundary.

Supabase is faked over HTTP (httpx.MockTransport), the model is a recording callable, and
the store commits to savepoints inside each test's transaction. Everything between those
boundaries is the real code.
"""

import base64
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote

import httpx
import psycopg

from postern.config import LLMConfig, load_config
from postern.core.store import Store
from postern.integrations.supabase_mgmt.catalog import STATEMENTS

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- runtime


class SavepointStore(Store):
    """A Store whose commits are savepoints inside the test's transaction.

    The runtime commits after every step and rolls back on failure. Here a commit releases
    and re-creates a savepoint and a rollback returns to it, so runtime tests keep those
    semantics and still leave nothing behind. (Deferred constraint triggers only fire at a
    real commit; Phase 2's tests cover them.)
    """

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        super().__init__(conn)
        conn.execute("savepoint runtime_commit")

    def commit(self) -> None:
        self.conn.execute("release savepoint runtime_commit")
        self.conn.execute("savepoint runtime_commit")

    def rollback(self) -> None:
        self.conn.execute("rollback to savepoint runtime_commit")


class SameTransaction:
    """Stands in for the gateway's own autocommit connection inside the test transaction."""

    autocommit = True

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def execute(self, query: Any, params: Any = None) -> Any:
        return self._conn.execute(query, params)


def jwt(claims: dict[str, Any]) -> str:
    """An unsigned JWT-shaped string with these claims (only the payload is ever read)."""

    def part(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"


class FakeModel:
    """A stand-in for litellm.completion that records every call."""

    def __init__(self, text: str = "[]", failing_models: Iterable[str] = ()) -> None:
        self.text = text
        self.failing = set(failing_models)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs["model"].split("/", 1)[1] in self.failing:
            raise RuntimeError("provider unavailable")
        message = SimpleNamespace(content=self.text, tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=40),
        )

    @staticmethod
    def cost(response: Any) -> float:
        return 0.0001


def llm_config_for_tests(**overrides: Any) -> LLMConfig:
    return load_config().llm.model_copy(update={"monthly_cap_usd": 1000.0} | overrides)


# ------------------------------------------------------------------- fake Supabase


@dataclass
class Relation:
    """One table or view in a fake project, with what an anonymous visitor could see."""

    schema: str
    name: str
    kind: str = "r"  # pg_class.relkind: r = table, v = view
    rls_enabled: bool = True
    security_invoker: bool = False
    policies: list[dict[str, Any]] = field(default_factory=list)
    granted_to: tuple[str, ...] = ("anon", "authenticated")  # Supabase's default privileges
    visible_rows: int = 0  # rows the REST API would show an anonymous visitor
    rows: list[dict[str, Any]] = field(default_factory=list)  # served only to a GET


def anon_policy(
    name: str, qual: str, *, cmd: str = "SELECT", permissive: str = "PERMISSIVE"
) -> dict[str, Any]:
    return {
        "policy": name,
        "permissive": permissive,
        "roles": ["anon"],
        "cmd": cmd,
        "qual": qual,
        "with_check": qual if cmd in ("ALL", "INSERT", "UPDATE") else None,
    }


def advisor_lint(
    name: str, level: str, schema: str, table: str, kind: str = "table"
) -> dict[str, Any]:
    """A lint in the shape the Management API spec declares (fetched 2026-10-14)."""
    return {
        "name": name,
        "title": name.replace("_", " ").title(),
        "level": level,
        "facing": "EXTERNAL",
        "categories": ["SECURITY"],
        "description": "test lint",
        "detail": f"{kind} {schema}.{table}",
        "remediation": "https://supabase.com/docs/guides/database/database-linter",
        "metadata": {"schema": schema, "name": table, "type": kind},
        "cache_key": f"{name}_{schema}_{table}",
    }


ROW_CANARY = "canary-row-value-7f3a@example.com"


def sample_project() -> list[Relation]:
    """The ADR-008 testbed (docs/runbooks/testbed.sql): three planted exposures and four
    clean near-misses."""
    rows = [{"id": 1, "email": ROW_CANARY}]
    return [
        # planted
        Relation("public", "notes", rls_enabled=False, visible_rows=37, rows=rows),
        Relation(
            "public",
            "profiles",
            policies=[anon_policy("Enable all access", "true", cmd="ALL")],
            visible_rows=12,
            rows=rows,
        ),
        Relation("public", "orders"),  # closed on its own; the view above leaks it
        Relation("public", "order_summary", kind="v", visible_rows=5, rows=rows),
        # clean near-misses
        Relation(
            "public",
            "todos",
            policies=[anon_policy("owners read their todos", "(auth.uid() = user_id)")],
        ),
        Relation(
            "public",
            "countries",
            policies=[anon_policy("public reference data", "true")],
            visible_rows=250,  # public by design
        ),
        Relation("public", "audit_trail"),  # RLS on and no policy: nobody can read it
        Relation("private", "staff_notes", rls_enabled=False, granted_to=()),
    ]


def sample_advisor_lints() -> list[dict[str, Any]]:
    """What the advisor reports for the testbed. Its lint list has nothing for USING (true)."""
    return [
        advisor_lint("rls_disabled_in_public", "ERROR", "public", "notes"),
        advisor_lint("security_definer_view", "ERROR", "public", "order_summary", "view"),
        advisor_lint("rls_enabled_no_policy", "INFO", "public", "audit_trail"),
    ]


# (finding_type, table) for every planted issue; nothing else may be reported.
PLANTED = {
    ("rls_disabled_in_public", "notes"),
    ("rls_policy_always_true", "profiles"),
    ("security_definer_view", "order_summary"),
}


class FakeSupabase:
    """A Supabase project as Postern sees it over HTTP: the Management API and PostgREST.

    `advisors` is the advisor endpoint's JSON body, or an int HTTP status to fail with;
    `catalog_status` makes every read-only query fail with that status.
    """

    def __init__(
        self,
        ref: str,
        relations: list[Relation],
        *,
        exposed_schemas: Iterable[str] = ("public",),
        advisors: Any = None,
        catalog_status: int | None = None,
        temporary_access: str = "disabled",
        accepted_keys: Iterable[str] | None = None,
        rest_error: type[httpx.RequestError] | None = None,
        token_status: int = 200,
    ) -> None:
        self.ref = ref
        self.relations = relations
        self.exposed = list(exposed_schemas)
        self.advisors = {"lints": []} if advisors is None else advisors
        self.catalog_status = catalog_status
        self.temporary_access = temporary_access
        self.accepted_keys = None if accepted_keys is None else set(accepted_keys)
        self.rest_error = rest_error
        self.token_status = token_status
        self.token_requests: list[dict[str, str]] = []
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def calls(self, host: str | None = None) -> list[tuple[str, str, str]]:
        """(method, host, path) of every request, optionally for one host."""
        return [
            (r.method, r.url.host, unquote(r.url.path))
            for r in self.requests
            if host is None or r.url.host == host
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.supabase.com":
            return self._management(request)
        if request.url.host == f"{self.ref}.supabase.co":
            return self._rest(request)
        return httpx.Response(404)

    def project(self) -> dict[str, Any]:
        return {
            "id": self.ref,
            "ref": self.ref,
            "organization_id": "testorg",
            "organization_slug": "testorg",
            "name": "testbed",
            "region": "us-east-1",
            "created_at": "2026-10-14T00:00:00Z",
            "status": "ACTIVE_HEALTHY",
            "database": {
                "host": f"db.{self.ref}.supabase.co",
                "version": "17.6",
                "postgres_engine": "17",
                "release_channel": "ga",
            },
        }

    def _token(self, request: httpx.Request) -> httpx.Response:
        """POST /v1/oauth/token: form-encoded, HTTP Basic client authentication."""
        form = dict(httpx.QueryParams(request.content.decode()))
        self.token_requests.append(form | {"authorization": request.headers["authorization"]})
        if self.token_status != 200:
            return httpx.Response(self.token_status, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={
                "access_token": "sbp_oauth_access_" + form.get("code", "refreshed"),
                "refresh_token": "refresh-" + form.get("code", "rotated"),
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    def _management(self, request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("POST", "/v1/oauth/token"):
            return self._token(request)
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return httpx.Response(401)
        prefix = f"/v1/projects/{self.ref}"
        method, path = request.method, request.url.path
        if (method, path) == ("GET", "/v1/projects"):
            return httpx.Response(200, json=[self.project()])
        if (method, path) == ("GET", prefix):
            return httpx.Response(200, json=self.project())
        if (method, path) == ("GET", f"{prefix}/jit-access"):
            return httpx.Response(200, json={"state": self.temporary_access})
        if (method, path) == ("GET", f"{prefix}/advisors/security"):
            if isinstance(self.advisors, int):
                return httpx.Response(self.advisors)
            return httpx.Response(200, json=self.advisors)
        if (method, path) == ("POST", f"{prefix}/database/query/read-only"):
            if self.catalog_status is not None:
                return httpx.Response(self.catalog_status)
            return self._read_only_query(json.loads(request.content)["query"])
        return httpx.Response(404)

    def _read_only_query(self, sql: str) -> httpx.Response:
        name = {statement: key for key, statement in STATEMENTS.items()}.get(sql)
        rows: list[dict[str, Any]]
        if name == "exposed_schemas":
            rows = [{"schema": schema} for schema in self.exposed]
        elif name == "relations":
            rows = [
                {
                    "schema": r.schema,
                    "name": r.name,
                    "kind": r.kind,
                    "rls_enabled": r.rls_enabled,
                    "security_invoker": r.security_invoker,
                }
                for r in self.relations
            ]
        elif name == "policies":
            rows = [
                {"schema": r.schema, "name": r.name} | p for r in self.relations for p in r.policies
            ]
        elif name == "api_role_grants":
            rows = [
                {"schema": r.schema, "name": r.name, "grantee": role, "privilege_type": "SELECT"}
                for r in self.relations
                for role in r.granted_to
            ]
        else:
            return httpx.Response(400, json={"message": "unexpected statement"})
        return httpx.Response(201, json=rows)

    def _rest(self, request: httpx.Request) -> httpx.Response:
        if self.rest_error is not None:
            raise self.rest_error("simulated network failure", request=request)
        if (
            self.accepted_keys is not None
            and request.headers.get("apikey") not in self.accepted_keys
        ):
            return httpx.Response(401, json={"message": "Invalid API key"})
        if request.url.path == "/rest/v1/":
            return httpx.Response(200, json={"swagger": "2.0"})
        schema = request.headers.get("accept-profile", "public")
        if schema not in self.exposed:
            return httpx.Response(406, json={"code": "PGRST106"})
        table = unquote(request.url.path.removeprefix("/rest/v1/"))
        relation = next((r for r in self.relations if (r.schema, r.name) == (schema, table)), None)
        if relation is None:
            return httpx.Response(404, json={"code": "PGRST205"})
        if "anon" not in relation.granted_to:
            return httpx.Response(401, json={"code": "42501"})
        headers = {"content-range": f"*/{relation.visible_rows}"}
        if request.method == "GET":
            return httpx.Response(200, headers=headers, json=relation.rows)
        return httpx.Response(200, headers=headers)


# ------------------------------------------------------------------ Sentinel world


class SentinelWorld:
    """Security Sentinel wired to the real runtime, store, gateway and toolkit, with only
    the outside world faked: Supabase over HTTP, the model, and the token broker."""

    def __init__(
        self,
        db: psycopg.Connection[Any],
        seed: Any,
        relations: list[Relation] | None = None,
        *,
        model_text: str | None = None,
        token_for: Callable[[str], str] | None = None,
        **supabase: Any,
    ) -> None:
        from postern.agents.security_sentinel import SecuritySentinel
        from postern.agents.toolkit import build_toolkit
        from postern.core.runtime import Runtime
        from postern.integrations.probe import ExposureProbe
        from postern.llm.gateway import LLMGateway

        config = load_config()
        seed.registry()
        self.db = db
        self.seed = seed
        self.user = seed.user()
        self.connection_id = seed.active_connection(self.user)
        self.ref = seed.project_ref(self.connection_id)
        supabase.setdefault("advisors", {"lints": sample_advisor_lints()})
        self.supabase = FakeSupabase(
            self.ref, sample_project() if relations is None else relations, **supabase
        )
        self.http = self.supabase.client()
        self.model = FakeModel(
            model_text
            if model_text is not None
            else json.dumps([{"index": i, "text": f"Plain explanation {i}."} for i in range(20)])
        )
        self.store = SavepointStore(db)
        self.gateway = LLMGateway(
            SameTransaction(db),  # type: ignore[arg-type]
            llm_config_for_tests(),
            "test-project",
            completion=self.model,
            cost=FakeModel.cost,
        )
        self.tool_calls: list[str] = []
        tools = build_toolkit(
            token_for or (lambda connection_id: "test-access-token"),
            self.http,
            ExposureProbe(self.http, config.probe, sleep=lambda seconds: None),
        )
        self.tools = {name: self._counted(name, tool) for name, tool in tools.items()}
        self.runtime = Runtime(
            self.store, self.gateway, self.tools, config.agent_limits, visibility_seconds=600
        )
        self.agent = SecuritySentinel(config.probe)

    def _counted(self, name: str, tool: Any) -> Any:
        def counted(*args: Any) -> Any:
            self.tool_calls.append(name)
            return tool(*args)

        return counted

    def enqueue(self) -> str:
        run_id = self.seed.run("security_sentinel", connection=self.connection_id)
        self.store.commit()
        return run_id

    def claim(self) -> Any:
        claimed = self.store.claim_next_run("tier0", 600, 5)
        self.store.commit()
        assert claimed is not None, "no run to claim"
        return claimed

    def run(self) -> Any:
        """Enqueue, claim and execute one run; returns (run_id, outcome)."""
        run_id = self.enqueue()
        claimed = self.claim()
        assert claimed.run_id == run_id
        return run_id, self.runtime.execute(claimed, self.agent)

    def findings(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "select finding_type, object_schema, object_name, severity, source,"
            " probe_confirmed, probe_row_count, explanation from postern.findings"
            " where connection_id = %s order by object_name",
            (self.connection_id,),
        ).fetchall()
        keys = (
            "finding_type",
            "object_schema",
            "object_name",
            "severity",
            "source",
            "probe_confirmed",
            "probe_row_count",
            "explanation",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def run_status(self, run_id: str) -> tuple[str, str | None]:
        row = self.db.execute(
            "select status::text, error_code from postern.runs where id = %s", (run_id,)
        ).fetchone()
        assert row
        return str(row[0]), row[1]

    def audit_events(self, run_id: str) -> list[str]:
        rows = self.db.execute(
            "select event_type from postern.audit_log where run_id = %s order by chain_position",
            (run_id,),
        ).fetchall()
        return [str(row[0]) for row in rows]


# ---------------------------------------------------------------------- connect API


class TransactionDatabase:
    """The connect API's `database` dependency, inside the test's transaction.

    Each `with db() as conn` block is a savepoint; `conn.commit()` keeps work so far, and an
    exception (or an aborted statement) undoes the block, as psycopg's own context manager
    would for a real connection.
    """

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    def __call__(self) -> "TransactionDatabase":
        return self

    def __enter__(self) -> "TransactionDatabase":
        self.conn.execute("savepoint api_block")
        return self

    def __exit__(self, kind: Any, error: Any, traceback: Any) -> None:
        failed = self.conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR
        if kind is not None or failed:
            self.conn.execute("rollback to savepoint api_block")
        self.conn.execute("release savepoint api_block")

    def execute(self, query: Any, params: Any = None) -> Any:
        return self.conn.execute(query, params)

    def commit(self) -> None:
        self.conn.execute("release savepoint api_block")
        self.conn.execute("savepoint api_block")


API_URL = "https://postern-api.test"
WORKER_IDENTITY = "postern-worker@postern-prod.iam.gserviceaccount.com"


def connect_api(db: psycopg.Connection[Any], fake: "FakeSupabase", cipher: Any) -> Any:
    """postern-api's connect routes, wired to the test transaction and a fake Supabase.

    Returns a TestClient on https (the state cookie is Secure). The worker identity check
    accepts the token "worker-identity" as the worker and anything else as a stranger.
    """
    from fastapi.testclient import TestClient

    from postern.api import connect
    from postern.api.app import app

    settings = connect.Settings(
        database_url="postgresql://unused",
        api_url=API_URL,
        client_id="11111111-2222-4333-8444-555555555555",
        client_secret="test-client-secret",
        worker_identity=WORKER_IDENTITY,
        cipher=cipher,
    )

    def verify(token: str, audience: str) -> str:
        assert audience == API_URL
        if token == "worker-identity":
            return WORKER_IDENTITY
        if token == "stranger-identity":
            return "someone-else@example.com"
        raise ValueError("bad token")

    app.dependency_overrides = {
        connect.settings: lambda: settings,
        connect.database: lambda: TransactionDatabase(db),
        connect.http_client: fake.client,
        connect.identity_verifier: lambda: verify,
    }
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def create_connect_link(db: psycopg.Connection[Any], user_id: str, project_ref: str) -> str:
    """What `postern connect link` does: returns the single-use token."""
    import secrets

    from postern.integrations.supabase_mgmt.oauth import sha256_hex

    token = secrets.token_urlsafe(32)
    db.execute(
        "select postern.create_connect_request(%s, %s, %s, 60)",
        (user_id, project_ref, sha256_hex(token)),
    )
    return token
