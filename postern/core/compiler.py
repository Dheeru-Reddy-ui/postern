"""Compile the validated registry into per-agent tool manifests (invariant 5).

A manifest is the complete, canonical statement of what one agent may do. Its SHA-256 is
stored on every run, so a run can always be traced to the exact grants it ran under. The
runtime (Phase 3) refuses any tool call the manifest doesn't list.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from postern.core.registry import AgentSpec, Registry
from postern.core.tools import TOOL_CATALOG, Tier

MANIFEST_FORMAT = 1
LOCK_PATH = Path("agents/manifests.lock.json")


@dataclass(frozen=True)
class CompiledAgent:
    name: str
    spec: AgentSpec
    manifest: Mapping[str, Any]
    manifest_hash: str

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.spec.tools


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compile_agent(name: str, spec: AgentSpec, registry_version: int) -> CompiledAgent:
    manifest: dict[str, Any] = {
        "manifest_format": MANIFEST_FORMAT,
        "registry_version": registry_version,
        "agent": name,
        "description": spec.description,
        "owner": spec.owner,
        "tier": spec.tier,
        "tier_name": Tier(spec.tier).name.lower(),
        "status": spec.status,
        "schedule": spec.schedule,
        "human_gate": spec.human_gate,
        "notification_budget": spec.notification_budget.model_dump(),
        "tools": [
            {"name": tool, "description": TOOL_CATALOG[tool].description}
            for tool in sorted(spec.tools)
        ],
    }
    digest = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    return CompiledAgent(name=name, spec=spec, manifest=manifest, manifest_hash=digest)


def compile_registry(registry: Registry) -> dict[str, CompiledAgent]:
    return {
        name: compile_agent(name, spec, registry.version)
        for name, spec in sorted(registry.agents.items())
    }


def lock_document(compiled: Mapping[str, CompiledAgent]) -> dict[str, Any]:
    """The reviewable snapshot committed as agents/manifests.lock.json."""
    return {
        "generated_by": "postern registry compile --write",
        "agents": {
            name: {"manifest_hash": agent.manifest_hash, "manifest": agent.manifest}
            for name, agent in sorted(compiled.items())
        },
    }


def render_lock(compiled: Mapping[str, CompiledAgent]) -> str:
    return json.dumps(lock_document(compiled), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
