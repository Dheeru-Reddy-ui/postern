"""Threat-model tests: TM-01. See docs/threat-model.md."""

import dataclasses
from typing import Any

from support import ROW_CANARY, FakeSupabase, Relation

from postern.agents.toolkit import _FINDING_FIELDS
from postern.config import load_config
from postern.core.store import ConnectionRecord
from postern.integrations.probe import ExposureProbe, ProbeResult

REF = "abcdefghijklmnopqrst"
ALLOWED = {"table", "http_status", "row_count", "probed_at"}


def _connection(key: str = "sb_publishable_test_key_123") -> ConnectionRecord:
    return ConnectionRecord("c1", "u1", REF, "active", key, "2026-10-14T00:00:00+00:00")


def _probe(fake: FakeSupabase) -> ExposureProbe:
    return ExposureProbe(fake.client(), load_config().probe, sleep=lambda seconds: None)


def test_probe_result_has_only_allowed_fields() -> None:
    """The probe result type has exactly four fields: table, HTTP status, row count capped at
    1000, and timestamp. Nothing else can be persisted."""
    assert {f.name for f in dataclasses.fields(ProbeResult)} == ALLOWED

    rows = [{"email": ROW_CANARY}] * 3
    fake = FakeSupabase(REF, [Relation("public", "big", visible_rows=250_000, rows=rows)])
    result = _probe(fake).probe(_connection(), "public", "big")
    output: dict[str, Any] = result.as_output()
    assert set(output) == ALLOWED
    assert output["table"] == "public.big"
    assert output["http_status"] == 200
    assert output["row_count"] == 1000, "counts are capped at 1000"
    assert ROW_CANARY not in repr(output)

    # What becomes a finding has no field that could hold row content either.
    assert {
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
    } == _FINDING_FIELDS


def test_probe_never_reads_response_rows() -> None:
    """Probes ask PostgREST for a count only: a HEAD request with `Prefer: count=estimated`
    (PostgREST's documented counting header, checked 2026-10-14), so no row body is ever
    received. The fake would serve rows to a GET; the probe never sends one."""
    rows = [{"email": ROW_CANARY}]
    fake = FakeSupabase(
        REF,
        [
            Relation("public", "notes", rls_enabled=False, visible_rows=37, rows=rows),
            Relation("internal", "jobs", rls_enabled=False, visible_rows=2, rows=rows),
        ],
        exposed_schemas=("public", "internal"),
    )
    probe = _probe(fake)
    results = [probe.probe(_connection(), "public", "notes")]
    results.append(probe.probe(_connection(), "internal", "jobs"))

    assert [r.row_count for r in results] == [37, 2]
    assert [r.method for r in fake.requests] == ["HEAD", "HEAD"]
    for request in fake.requests:
        assert request.headers["prefer"] == "count=estimated"
        assert request.url.params["select"] == "*"
    assert fake.requests[1].headers["accept-profile"] == "internal"
    assert ROW_CANARY not in repr([r.as_output() for r in results])
