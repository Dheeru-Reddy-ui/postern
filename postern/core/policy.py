"""The policy gate: every tool call is checked against the agent's compiled grant (invariant 5).

The grant comes from the manifest synced into postern.agents, never from the model or from
anything a run reads, so untrusted content can't widen it (TM-02).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from postern.core.tools import TOOL_CATALOG, Tier


class PolicyRefusal(Exception):
    def __init__(self, agent: str, tool: str, reason: str) -> None:
        super().__init__(f"{agent} may not call {tool}: {reason}")
        self.agent = agent
        self.tool = tool
        self.reason = reason


@dataclass(frozen=True)
class Grant:
    agent: str
    tier: Tier
    tools: frozenset[str]
    human_gate: bool
    manifest_hash: str

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any], manifest_hash: str) -> "Grant":
        return cls(
            agent=str(manifest["agent"]),
            tier=Tier(int(manifest["tier"])),
            tools=frozenset(str(tool["name"]) for tool in manifest["tools"]),
            human_gate=bool(manifest["human_gate"]),
            manifest_hash=manifest_hash,
        )


class PolicyGate:
    def check(self, grant: Grant, tool: str) -> None:
        """Raise PolicyRefusal unless `tool` is granted, catalogued and allowed for the tier."""
        if tool not in grant.tools:
            raise PolicyRefusal(grant.agent, tool, "tool_not_in_grant")
        spec = TOOL_CATALOG.get(tool)
        if spec is None:
            raise PolicyRefusal(grant.agent, tool, "tool_not_in_catalog")
        if grant.tier not in spec.tiers:
            raise PolicyRefusal(grant.agent, tool, "tool_forbidden_for_tier")
