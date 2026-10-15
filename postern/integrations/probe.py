"""Exposure probe: can an anonymous visitor read this table? (TM-01, TM-04, TM-26)

The probe asks the project's own public REST API, with the public key the owner pasted, for a
row *count*. It sends HEAD requests, so no row ever comes back, and it records exactly four
things: the table, the HTTP status, the row count capped at 1000, and the time.

It only runs against a connection the owner verified. The connection comes from the run's
context, never from a tool argument, and requests are spaced out and time-limited so a probe
can't load a small project.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from postern.config import ProbeConfig
from postern.core.store import ConnectionRecord
from postern.integrations.supabase_mgmt.keys import classify_key


class ProbeRefused(PermissionError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    """Everything a probe may record. Nothing else exists to store (invariant 4)."""

    table: str
    http_status: int | None
    row_count: int | None
    probed_at: str

    def as_output(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "http_status": self.http_status,
            "row_count": self.row_count,
            "probed_at": self.probed_at,
        }


def _parse_count(content_range: str | None, cap: int) -> int | None:
    # PostgREST: "0-24/3573" or "*/3573"; "*" after the slash means unknown.
    if not content_range or "/" not in content_range:
        return None
    total = content_range.rsplit("/", 1)[1].strip()
    return min(int(total), cap) if total.isdigit() else None


def _safe_identifier(value: str) -> str:
    if not value or len(value) > 63 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ProbeRefused("not a valid Postgres identifier")
    return value


class ExposureProbe:
    def __init__(
        self,
        http: httpx.Client,
        config: ProbeConfig,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._config = config
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: dict[str, float] = {}

    def _wait_turn(self, project_ref: str) -> None:
        last = self._last_request.get(project_ref)
        if last is not None:
            remaining = self._config.min_interval_seconds - (self._monotonic() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[project_ref] = self._monotonic()

    def probe(self, connection: ConnectionRecord | None, schema: str, table: str) -> ProbeResult:
        if connection is None or connection.status != "active" or not connection.verified_at:
            raise ProbeRefused("probes only run on connections their owner verified")
        if not connection.publishable_key:
            raise ProbeRefused("the owner hasn't provided the project's public key")
        key = connection.publishable_key
        kind = classify_key(key)
        schema, table = _safe_identifier(schema), _safe_identifier(table)

        headers = {
            "apikey": key,
            "Prefer": "count=estimated",
            "User-Agent": self._config.user_agent,
        }
        if kind == "legacy_anon":
            headers["Authorization"] = f"Bearer {key}"
        if schema != "public":
            headers["Accept-Profile"] = schema

        self._wait_turn(connection.project_ref)
        url = (
            f"https://{connection.project_ref}.supabase.co/rest/v1/{quote(table, safe='')}?select=*"
        )
        try:
            response = self._http.head(url, headers=headers, timeout=self._config.timeout_seconds)
            status: int | None = response.status_code
            count = (
                _parse_count(response.headers.get("content-range"), self._config.row_count_cap)
                if status in (200, 206)
                else None
            )
        except httpx.HTTPError:
            status, count = None, None
        return ProbeResult(
            table=f"{schema}.{table}",
            http_status=status,
            row_count=count,
            probed_at=datetime.now(UTC).isoformat(),
        )
