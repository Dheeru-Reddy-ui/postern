"""agents/registry.yaml: schema and validation (invariant 5, TM-23).

The validator rejects any agent that is missing a field, grants a tool its tier may not hold,
names a tool that doesn't exist, or breaks a tier rule. Nothing unvalidated reaches the
compiler or the database.
"""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from postern.core.tools import TOOL_CATALOG, Tier

REGISTRY_PATH = Path("agents/registry.yaml")
AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class RegistryError(ValueError):
    """The registry file is unreadable or invalid. The message lists every problem."""


class NotificationBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    realtime_alerts_per_7_days: int = Field(ge=0, le=1)
    digests_per_7_days: int = Field(ge=0, le=1)


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    description: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    tier: int = Field(ge=0, le=3)
    status: Literal["active", "planned"]
    schedule: Literal["nightly", "daily"] | None = None
    human_gate: bool
    tools: list[str]
    notification_budget: NotificationBudget

    @property
    def tier_enum(self) -> Tier:
        return Tier(self.tier)

    @model_validator(mode="after")
    def _check_tier_rules(self) -> "AgentSpec":
        problems: list[str] = []
        if len(set(self.tools)) != len(self.tools):
            problems.append("tools are listed more than once")
        for tool in self.tools:
            spec = TOOL_CATALOG.get(tool)
            if spec is None:
                problems.append(f"tool {tool!r} is not in the tool catalog")
            elif self.tier_enum not in spec.tiers:
                problems.append(f"tool {tool!r} is forbidden for tier {self.tier}")
        if self.tier_enum is Tier.DRAFT and not self.human_gate:
            problems.append("tier 1 agents must have human_gate: true")
        budget = self.notification_budget
        if self.tier_enum is not Tier.CONTACT and (
            budget.realtime_alerts_per_7_days or budget.digests_per_7_days
        ):
            problems.append("only tier 2 agents may have a non-zero notification budget")
        if problems:
            raise ValueError("; ".join(problems))
        return self


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    agents: dict[str, AgentSpec]

    @field_validator("agents")
    @classmethod
    def _check_names(cls, agents: dict[str, AgentSpec]) -> dict[str, AgentSpec]:
        bad = [name for name in agents if not AGENT_NAME.fullmatch(name)]
        if bad:
            raise ValueError(f"invalid agent names {bad}: use lowercase, digits and _")
        if not agents:
            raise ValueError("the registry has no agents")
        return agents


def parse_registry(text: str) -> Registry:
    """Validate registry YAML text. Raises RegistryError listing every problem."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise RegistryError(f"registry is not valid YAML: {error}") from error
    try:
        return Registry.model_validate(data)
    except ValidationError as error:
        lines = [
            f"- {'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
            for issue in error.errors()
        ]
        raise RegistryError("invalid agent registry:\n" + "\n".join(lines)) from error


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    return parse_registry(path.read_text(encoding="utf-8"))
