"""Threat-model tests: TM-26. See docs/threat-model.md."""

import inspect
from collections.abc import Callable
from typing import Any

import httpx
from support import FakeSupabase, Relation, SentinelWorld

from postern.config import load_config
from postern.core.store import ConnectionRecord
from postern.integrations import probe as probe_module
from postern.integrations.probe import ExposureProbe

REF_A = "aaaaaaaaaaaaaaaaaaaa"
REF_B = "bbbbbbbbbbbbbbbbbbbb"


def _connection(ref: str) -> ConnectionRecord:
    return ConnectionRecord("c", "u", ref, "active", "sb_publishable_k_123456", "2026-10-14")


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_probe_rate_limit_is_enforced(terraform: Callable[[str], Any]) -> None:
    """A per-project rate limit and a concurrency of one."""
    config = load_config().probe
    assert config.min_interval_seconds >= 0.5
    clock = Clock()
    tables = [Relation("public", f"t{i}", rls_enabled=False, visible_rows=1) for i in range(3)]
    fake_a, fake_b = FakeSupabase(REF_A, tables), FakeSupabase(REF_B, tables)

    def route(request: httpx.Request) -> httpx.Response:
        fake = fake_a if request.url.host.startswith(REF_A) else fake_b
        return fake.handle(request)

    probe = ExposureProbe(
        httpx.Client(transport=httpx.MockTransport(route)),
        config,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    probe.probe(_connection(REF_A), "public", "t0")
    probe.probe(_connection(REF_B), "public", "t0")  # another project: no wait
    assert clock.slept == []
    probe.probe(_connection(REF_A), "public", "t1")  # same project, immediately: waits
    assert clock.slept == [config.min_interval_seconds]
    clock.now += 60
    probe.probe(_connection(REF_A), "public", "t2")  # enough time passed: no wait
    assert clock.slept == [config.min_interval_seconds]

    # Concurrency of one: the probe is synchronous, and exactly one worker task runs at a time.
    assert not any(inspect.iscoroutinefunction(f) for _, f in inspect.getmembers(ExposureProbe))
    assert "thread" not in inspect.getsource(probe_module).lower()
    worker = terraform("infra/terraform/gcp").resources("google_cloud_run_v2_job")["worker"]
    assert worker["template"][0]["parallelism"] == 1
    assert worker["template"][0]["task_count"] == 1


def test_probe_uses_only_read_http_methods(sentinel_world: Callable[..., SentinelWorld]) -> None:
    """Probes use only read methods (GET or HEAD). Write exposure is inferred from the schema,
    never tested by writing."""
    world = sentinel_world()
    world.run()
    rest = world.supabase.calls(f"{world.ref}.supabase.co")
    assert rest, "the planted tables were probed"
    assert {method for method, _, _ in rest} == {"HEAD"}
    # The Management API is read too: GETs, plus the read-only query endpoint's POST.
    for method, _, path in world.supabase.calls("api.supabase.com"):
        assert method == "GET" or path.endswith("/database/query/read-only"), (method, path)


def test_probe_count_is_bounded_and_times_out() -> None:
    """The count strategy is bounded (estimated rather than exact on large tables), with a hard
    timeout."""
    config = load_config().probe
    assert 0 < config.timeout_seconds <= 15
    assert config.row_count_cap == 1000
    fake = FakeSupabase(REF_A, [Relation("public", "huge", rls_enabled=False, visible_rows=10**8)])
    probe = ExposureProbe(fake.client(), config, sleep=lambda seconds: None)
    result = probe.probe(_connection(REF_A), "public", "huge")
    assert result.row_count == 1000
    (request,) = fake.requests
    assert request.headers["prefer"] == "count=estimated"
    timeouts = request.extensions["timeout"]
    assert set(timeouts.values()) == {config.timeout_seconds}

    # A probe that times out is recorded as "no answer", never as "not exposed" or a crash.
    slow = FakeSupabase(
        REF_A,
        [Relation("public", "slow", rls_enabled=False, visible_rows=5)],
        rest_error=httpx.ReadTimeout,
    )
    timed_out = ExposureProbe(slow.client(), config, sleep=lambda seconds: None)
    result = timed_out.probe(_connection(REF_A), "public", "slow")
    assert (result.http_status, result.row_count) == (None, None)
