"""Threat-model tests: TM-28. See docs/threat-model.md."""

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


LLM_PACKAGES = {"litellm", "openai", "anthropic", "google-genai", "google-cloud-aiplatform"}


def _closure(packages: dict[str, Any], roots: set[str]) -> set[str]:
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(dep["name"] for dep in packages[name].get("dependencies", []))
    return seen


def test_llm_sdk_installed_only_in_worker_image(terraform: Callable[[str], Any]) -> None:
    """LiteLLM and provider SDKs are installed only in the worker image, which can read only the
    worker's secrets."""
    # In the lock: nothing the API needs pulls in an LLM SDK; only the worker extra does.
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    postern = packages["postern"]
    base = _closure(packages, {dep["name"] for dep in postern["dependencies"]})
    assert not base & LLM_PACKAGES, base & LLM_PACKAGES
    worker_extra = {dep["name"] for dep in postern["optional-dependencies"]["worker"]}
    assert "litellm" in _closure(packages, worker_extra)

    # In the Dockerfile: only the worker stage installs the extra, and each final image
    # copies its own environment.
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    stage = re.compile(r"^FROM \S+ AS (\S+)\n(.*?)(?=^FROM |\Z)", re.MULTILINE | re.DOTALL)
    stages = dict(stage.findall(dockerfile))
    assert "--extra" not in stages["deps-api"]
    assert "--extra worker" in stages["deps-worker"]
    assert "--from=deps-api" in stages["api"]
    assert "deps-worker" not in stages["api"]
    assert "--from=deps-worker" in stages["worker"]
    assert "--all-extras" not in dockerfile

    # In CI: the API image is checked to contain no LiteLLM, and each component deploys its
    # own image.
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "find_spec('litellm') is not None" in ci
    assert 'postern-api --image "${API_IMAGE}"' in ci
    assert 'postern-worker --image "${WORKER_IMAGE}"' in ci

    # And the worker, which runs the LLM code, can read only its own database URL.
    secrets = json.loads(
        (REPO_ROOT / "infra/terraform/gcp/secret_access.json").read_text(encoding="utf-8")
    )["secrets"]
    readable = {name for name, spec in secrets.items() if "worker" in spec["readers"]}
    assert readable == {"control-plane-db-url-worker"}
    assert set(terraform("infra/terraform/gcp").locals()["worker_secret_env"].values()) == readable
