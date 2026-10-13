"""Phase 1 hello-world components that prove the deploy pipeline."""

import json

import pytest
from fastapi.testclient import TestClient

from postern.__main__ import main as entrypoint
from postern.api.app import app
from postern.core.worker import main as worker_main


def test_health_reports_ok_and_git_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTERN_GIT_SHA", "abc123")
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "component": "api", "git_sha": "abc123"}


def test_api_docs_are_not_exposed() -> None:
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_worker_logs_one_structured_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert worker_main() == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["component"] == "worker"
    assert record["severity"] == "INFO"


def test_entrypoint_rejects_unknown_component(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTERN_COMPONENT", "nope")
    assert entrypoint() == 2
