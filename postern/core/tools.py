"""The tool catalog: every tool an agent can be granted, and which tiers may hold it.

A tool that isn't listed here can't be granted, so the capabilities CLAUDE.md forbids for
every tier (writing to a user's database, merging, disabling RLS, reading or creating
secrets or API keys, deleting anything in a user's project) have no entry at all.
"""

from dataclasses import dataclass
from enum import IntEnum


class Tier(IntEnum):
    READ_AND_REPORT = 0
    DRAFT = 1
    CONTACT = 2
    META = 3


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    tiers: frozenset[Tier]


_TIER_0 = frozenset({Tier.READ_AND_REPORT})

_TOOLS = (
    # Tier 0: read and report (ADR-005 lists the Supabase endpoints behind these).
    ToolSpec("supabase.project.read", "Read the connected project's metadata and health.", _TIER_0),
    ToolSpec("supabase.advisors.security", "Read the project's security advisor lints.", _TIER_0),
    ToolSpec(
        "supabase.advisors.performance", "Read the project's performance advisor lints.", _TIER_0
    ),
    ToolSpec(
        "supabase.schema.read",
        "Read schema shape: tables, columns, exposed schemas, storage bucket flags.",
        _TIER_0,
    ),
    ToolSpec(
        "supabase.query.allowlisted",
        "Run one reviewed, allowlisted catalog query through the read-only endpoint.",
        _TIER_0,
    ),
    ToolSpec(
        "probe.exposure",
        "Ask the project's REST API, with the public key, how many rows anyone can see.",
        _TIER_0,
    ),
    ToolSpec("findings.record", "Record a finding for the connected project.", _TIER_0),
    ToolSpec(
        "findings.explain",
        "Attach a plain-language explanation to a finding. Can't change what was found.",
        _TIER_0,
    ),
    ToolSpec(
        "findings.read",
        "Read findings for the connected project.",
        frozenset({Tier.READ_AND_REPORT, Tier.DRAFT, Tier.CONTACT}),
    ),
    # Tier 1: draft.
    ToolSpec(
        "proposals.record", "Record a drafted fix and its validation.", frozenset({Tier.DRAFT})
    ),
    ToolSpec(
        "github.push_postern_branch",
        "Push a commit to a postern/* branch on a verified repo.",
        frozenset({Tier.DRAFT}),
    ),
    ToolSpec(
        "github.open_pull_request",
        "Open a pull request from a postern/* branch. A human merges.",
        frozenset({Tier.DRAFT}),
    ),
    ToolSpec(
        "sandbox.validate_fix",
        "Validate a drafted migration in the throwaway validator (ADR-007).",
        frozenset({Tier.DRAFT}),
    ),
    # Tier 2: contact, only through the notification service.
    ToolSpec("ledger.read", "Read the value ledger.", frozenset({Tier.CONTACT})),
    ToolSpec(
        "notify.send_digest",
        "Hand a digest to the notification service, which enforces the person's budget.",
        frozenset({Tier.CONTACT}),
    ),
    # Meta: Postern's own operations.
    ToolSpec("postern.metrics.read", "Read Postern's own metrics.", frozenset({Tier.META})),
    ToolSpec("postern.logs.read", "Read Postern's own logs.", frozenset({Tier.META})),
    ToolSpec(
        "github.open_issue_postern_repo",
        "Open an issue on Postern's own repository only.",
        frozenset({Tier.META}),
    ),
)

TOOL_CATALOG: dict[str, ToolSpec] = {tool.name: tool for tool in _TOOLS}

# Words that must never appear in a tool name (tests/unit/test_registry.py enforces this).
FORBIDDEN_CAPABILITY_WORDS: tuple[str, ...] = (
    "write_sql",
    "merge",
    "approve",
    "disable_rls",
    "api_key",
    "secret",
    "delete",
    "migration",
    "service_role",
)
