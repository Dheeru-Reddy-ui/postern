"""The committed registry, the tool catalog and the compiled manifest lock."""

from pathlib import Path

from postern.core.compiler import LOCK_PATH, compile_registry, render_lock
from postern.core.registry import REGISTRY_PATH, load_registry
from postern.core.tools import FORBIDDEN_CAPABILITY_WORDS, TOOL_CATALOG, Tier

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_committed_registry_is_valid() -> None:
    registry = load_registry(REPO_ROOT / REGISTRY_PATH)
    assert "security_sentinel" in registry.agents
    assert registry.agents["fix_drafter"].human_gate is True


def test_manifest_lock_is_up_to_date() -> None:
    """agents/manifests.lock.json must match the registry.

    Fix with `postern registry compile --write`."""
    compiled = compile_registry(load_registry(REPO_ROOT / REGISTRY_PATH))
    assert (REPO_ROOT / LOCK_PATH).read_text(encoding="utf-8") == render_lock(compiled)


def test_compilation_is_deterministic() -> None:
    registry = load_registry(REPO_ROOT / REGISTRY_PATH)
    first = {name: agent.manifest_hash for name, agent in compile_registry(registry).items()}
    second = {name: agent.manifest_hash for name, agent in compile_registry(registry).items()}
    assert first == second


def test_catalog_has_no_forbidden_capability() -> None:
    """CLAUDE.md forbids, for every tier: applying migrations to production, merging PRs,
    disabling RLS, reading or creating secrets or API keys, deleting in a user's project."""
    for name in TOOL_CATALOG:
        lowered = name.lower()
        assert not any(word in lowered for word in FORBIDDEN_CAPABILITY_WORDS), name


def test_every_tool_belongs_to_some_tier() -> None:
    for tool in TOOL_CATALOG.values():
        assert tool.tiers, tool.name
        assert tool.tiers <= set(Tier), tool.name


def test_manifest_lists_exactly_the_granted_tools() -> None:
    compiled = compile_registry(load_registry(REPO_ROOT / REGISTRY_PATH))
    sentinel = compiled["security_sentinel"]
    granted = sorted(sentinel.spec.tools)
    assert [tool["name"] for tool in sentinel.manifest["tools"]] == granted
    assert sentinel.allows("probe.exposure")
    assert not sentinel.allows("github.push_postern_branch")
    assert compiled["drill_echo"].manifest["tools"] == []
