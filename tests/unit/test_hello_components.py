"""The deployable components start, and refuse to start half-configured."""

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from postern.__main__ import main as entrypoint
from postern.api.app import app
from postern.core.redaction import configure_logging
from postern.core.worker import main as worker_main


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_health_reports_ok_and_git_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTERN_GIT_SHA", "abc123")
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "component": "api", "git_sha": "abc123"}


def test_api_docs_are_not_exposed() -> None:
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


@pytest.mark.usefixtures("restore_logging")
def test_worker_refuses_to_start_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("POSTERN_DATABASE_URL", "POSTERN_API_URL", "POSTERN_GCP_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="POSTERN_DATABASE_URL is not set"):
        worker_main()


@pytest.mark.usefixtures("restore_logging")
def test_logs_are_one_structured_line_each(capsys: pytest.CaptureFixture[str]) -> None:
    log = configure_logging("worker")
    log.critical("worker drained", extra={"run_id": "r1", "postern_alert": "run_failed"})
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["component"] == "worker"
    assert record["severity"] == "ALERT"
    assert (record["run_id"], record["postern_alert"]) == ("r1", "run_failed")


def test_entrypoint_rejects_unknown_component(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTERN_COMPONENT", "nope")
    assert entrypoint() == 2
