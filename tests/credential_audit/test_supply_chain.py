"""Threat-model tests: TM-28. See docs/threat-model.md."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
USES = re.compile(r"^\s*-?\s*uses:\s*(?P<action>[^@\s]+)@(?P<ref>\S+)", re.MULTILINE)
FULL_SHA = re.compile(r"[0-9a-f]{40}")
UV_INSTALL = re.compile(r"\buv (sync|run)\b")


def test_ci_installs_only_locked_dependencies() -> None:
    """CI installs only the hash-locked dependency set (uv sync --locked)."""
    assert 'hash = "sha256:' in (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert WORKFLOWS, "no workflows found"
    for workflow in WORKFLOWS:
        for line in workflow.read_text(encoding="utf-8").splitlines():
            command = line.split("#", 1)[0]
            where = f"{workflow.name}: {line.strip()}"
            assert "pip install" not in command, where
            assert "uvx " not in command, where
            if UV_INSTALL.search(command):
                assert "--locked" in command, where


def test_github_actions_pinned_to_commit_shas() -> None:
    """Every GitHub Action is pinned to a full commit SHA."""
    uses = [m for wf in WORKFLOWS for m in USES.finditer(wf.read_text(encoding="utf-8"))]
    assert uses, "no `uses:` steps found"
    for m in uses:
        assert FULL_SHA.fullmatch(m["ref"]), f"{m['action']}@{m['ref']} is not a commit SHA"


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-28 · prevention lands in Phase 3"
)
def test_llm_sdk_installed_only_in_worker_image() -> None:
    """LiteLLM and provider SDKs are installed only in the worker image, which can read only the
    worker's secrets."""
    raise NotImplementedError
