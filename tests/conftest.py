"""Shared fixtures."""

import os
import secrets
import string
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import hcl2
import psycopg
import pytest
from psycopg.types.json import Jsonb

from postern.core.compiler import compile_registry
from postern.core.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[1]


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text


def _clean(value: Any) -> Any:
    """python-hcl2 keeps HCL quoting and parser metadata; strip both to get plain values."""
    if isinstance(value, dict):
        return {
            _unquote(key): _clean(item)
            for key, item in value.items()
            if key not in ("__comments__", "__is_block__")
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        return _unquote(value)
    return value


class TerraformRoot:
    """Every *.tf file in one Terraform root module, parsed and merged."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.blocks: dict[str, list[Any]] = {}
        for path in sorted(directory.glob("*.tf")):
            with path.open(encoding="utf-8") as handle:
                parsed = _clean(hcl2.load(handle))
            for kind, entries in parsed.items():
                self.blocks.setdefault(kind, []).extend(entries)

    def resources(self, resource_type: str) -> dict[str, dict[str, Any]]:
        """Resource name -> body, for every resource of one type."""
        found: dict[str, dict[str, Any]] = {}
        for entry in self.blocks.get("resource", []):
            for name, body in entry.get(resource_type, {}).items():
                found[name] = body
        return found

    def resource_types(self) -> set[str]:
        return {kind for entry in self.blocks.get("resource", []) for kind in entry}

    def variables(self) -> dict[str, dict[str, Any]]:
        return {
            name: body for entry in self.blocks.get("variable", []) for name, body in entry.items()
        }

    def locals(self) -> dict[str, Any]:
        return {
            key: value for entry in self.blocks.get("locals", []) for key, value in entry.items()
        }


@pytest.fixture(scope="session")
def terraform() -> Callable[[str], TerraformRoot]:
    """Parse a Terraform root module by its path relative to the repo root."""
    cache: dict[str, TerraformRoot] = {}

    def load(relative: str) -> TerraformRoot:
        if relative not in cache:
            cache[relative] = TerraformRoot(REPO_ROOT / relative)
        return cache[relative]

    return load


# --------------------------------------------------------------------------- database

DATABASE_ENV = "POSTERN_TEST_DATABASE_URL"


@pytest.fixture(scope="session")
def database_url() -> str:
    """A disposable local Supabase database with every migration applied.

    Locally: `npx supabase@2.120.0 db start`, then
    POSTERN_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55322/postgres.
    CI always provides it, so database tests can never be silently skipped there.
    """
    url = os.environ.get(DATABASE_ENV)
    if not url:
        if os.environ.get("CI"):
            pytest.fail(f"{DATABASE_ENV} must be set in CI")
        pytest.skip(f"set {DATABASE_ENV} to run database tests (see tests/conftest.py)")
    return url


@pytest.fixture
def db(database_url: str) -> Iterator[psycopg.Connection[Any]]:
    """One transaction per test, always rolled back: tests leave nothing behind."""
    with psycopg.connect(database_url) as conn:
        try:
            yield conn
        finally:
            conn.rollback()


class Seed:
    """Builds control-plane fixtures inside the test's transaction."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    def agent(
        self,
        name: str,
        *,
        tier: int = 0,
        human_gate: bool = False,
        status: str = "active",
        schedule: str | None = None,
    ) -> str:
        manifest = {"agent": name, "tier": tier, "tools": [], "test": True}
        self.conn.execute(
            "select postern.sync_agent(%s, 'tests', %s::smallint, '{}'::text[], %s, %s, %s,"
            " %s::postern.agent_status, %s, %s, 1)",
            (
                name,
                tier,
                human_gate,
                Jsonb({"realtime_alerts_per_7_days": 0, "digests_per_7_days": 0}),
                schedule,
                status,
                Jsonb(manifest),
                "0" * 64,
            ),
        )
        return name

    def registry(self) -> None:
        """Sync the real agents/registry.yaml, as `postern registry sync` does."""
        compiled = compile_registry(load_registry(REPO_ROOT / "agents" / "registry.yaml"))
        for name, agent in compiled.items():
            spec = agent.spec
            self.conn.execute(
                "select postern.sync_agent(%s, %s, %s::smallint, %s::text[], %s, %s, %s,"
                " %s::postern.agent_status, %s, %s, 1)",
                (
                    name,
                    spec.owner,
                    spec.tier,
                    sorted(spec.tools),
                    spec.human_gate,
                    Jsonb(spec.notification_budget.model_dump()),
                    spec.schedule,
                    spec.status,
                    Jsonb(dict(agent.manifest)),
                    agent.manifest_hash,
                ),
            )

    def user(self) -> str:
        user_id = str(uuid.uuid4())
        self.conn.execute("insert into auth.users (id) values (%s)", (user_id,))
        self.conn.execute("insert into postern.users (id) values (%s)", (user_id,))
        return user_id

    def connection(self, user_id: str) -> str:
        ref = "".join(secrets.choice(string.ascii_lowercase) for _ in range(20))
        row = self.conn.execute(
            "insert into postern.connections (user_id, kind, supabase_project_ref)"
            " values (%s, 'supabase_project', %s) returning id",
            (user_id, ref),
        ).fetchone()
        assert row
        return str(row[0])

    def run(self, agent: str, *, key: str | None = None, connection: str | None = None) -> str:
        row = self.conn.execute(
            "select postern.enqueue_run(%s, 'drill', %s, %s)",
            (agent, key or f"test:{uuid.uuid4()}", connection),
        ).fetchone()
        assert row
        return str(row[0])

    def transition(self, run_id: str, to: str) -> None:
        self.conn.execute("select postern.transition_run(%s, %s)", (run_id, to))

    def finding(self, user_id: str, connection_id: str, agent: str, **overrides: Any) -> str:
        values = {
            "fingerprint": uuid.uuid4().hex * 2,
            "finding_type": "rls_disabled_in_public",
            "category": "security",
            "severity": "high",
            "source": "advisor",
            "probe_confirmed": False,
        } | overrides
        row = self.conn.execute(
            "insert into postern.findings (connection_id, user_id, agent, fingerprint,"
            " finding_type, category, severity, source, probe_confirmed)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                connection_id,
                user_id,
                agent,
                values["fingerprint"],
                values["finding_type"],
                values["category"],
                values["severity"],
                values["source"],
                values["probe_confirmed"],
            ),
        ).fetchone()
        assert row
        return str(row[0])


@pytest.fixture
def seed(db: psycopg.Connection[Any]) -> Seed:
    return Seed(db)
