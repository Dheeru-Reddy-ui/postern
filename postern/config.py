"""Runtime configuration from postern/runtime.yaml, plus environment for secrets and URLs."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

CONFIG_PATH = Path(__file__).resolve().parent / "runtime.yaml"


class Prices(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input: float = Field(ge=0)
    output: float = Field(ge=0)


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    location: str
    primary_model: str
    fallback_model: str
    reservation_prices_usd_per_million: dict[str, Prices]
    monthly_cap_usd: float = Field(gt=0)
    max_output_tokens: int = Field(gt=0, le=8192)
    timeout_seconds: float = Field(gt=0)
    reasoning_effort: Literal["disable", "minimal", "low"] = "disable"


class AgentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_steps: int = Field(gt=0)
    max_llm_calls_per_run: int = Field(ge=0)
    max_prompt_chars: int = Field(gt=0)


class ProbeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    min_interval_seconds: float = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    max_tables_per_run: int = Field(gt=0)
    row_count_cap: int = Field(gt=0, le=1000)
    user_agent: str


class WorkerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    visibility_timeout_seconds: int = Field(gt=0)
    max_attempts: int = Field(gt=0)
    stale_after_hours: int = Field(gt=0)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    llm: LLMConfig
    agent_limits: AgentLimits
    probe: ProbeConfig
    worker: WorkerConfig


@lru_cache(maxsize=1)
def load_config(path: Path = CONFIG_PATH) -> RuntimeConfig:
    return RuntimeConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def require_env(name: str) -> str:
    """A required environment variable. The value is never logged."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value
