"""Security Sentinel (Tier 0): find exposed data, confirm it, record findings (Phase 3).

Everything that decides *what* is found is deterministic code:
  1. read the project, then its security lints (Supabase's advisor merged with Postern's own
     catalog checks);
  2. probe every table or view a lint flags, anonymously, for a row count;
  3. record one finding per WARN/ERROR security lint. Severity is "critical" only when the
     probe saw rows (TM-14). The database enforces that too.
The LLM is used once, afterwards, to write plain-language explanations from facts (counts,
severities, names wrapped as untrusted data). It can't add, drop or change a finding (TM-16).

`next_action` depends only on the run's history, so a resumed run continues where it
stopped (TM-18).
"""

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

from postern.config import ProbeConfig
from postern.core.runtime import Action, Finish, ModelCall, RunContext, ToolCall
from postern.core.store import StepRecord
from postern.core.untrusted import Prompt, Untrusted

# Lints about data an anonymous visitor might reach: worth a probe and a finding.
EXPOSURE_LINTS = {
    "rls_disabled_in_public",
    "rls_policy_always_true",
    "policy_exists_rls_disabled",
    "security_definer_view",
    "sensitive_columns_exposed",
    "auth_users_exposed",
}
PROBEABLE_TYPES = {"table", "view", "materialized view"}
LEVEL_SEVERITY = {"ERROR": "high", "WARN": "medium"}

EXPLAIN_INSTRUCTIONS = (
    "You explain security findings to someone who built an app with an AI tool and has "
    "never written SQL. For each numbered finding below, write two or three calm, plain "
    "sentences: what it means for their app's data, and what to do next. Don't blame them, "
    "don't use jargon, and don't mention any table or column that isn't in the finding. "
    'Answer with JSON only: [{"index": <number>, "text": "<explanation>"}].'
)


def fingerprint(project_ref: str, finding_type: str, schema: str | None, name: str | None) -> str:
    raw = f"{project_ref}|{finding_type}|{schema or ''}|{name or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _outputs(history: Sequence[StepRecord], tool: str) -> list[Any]:
    return [s.output for s in history if s.tool_name == tool and s.status == "completed"]


class SecuritySentinel:
    name = "security_sentinel"

    def __init__(self, probe_config: ProbeConfig) -> None:
        self.max_probes = probe_config.max_tables_per_run

    def _reportable(self, lints: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            lint
            for lint in lints
            if lint["level"] in LEVEL_SEVERITY
            and "SECURITY" in [c.upper() for c in lint.get("categories", [])]
        ]

    def _probe_targets(self, lints: list[dict[str, Any]]) -> list[tuple[str, str]]:
        targets = sorted(
            {
                (str(lint["metadata"]["schema"]), str(lint["metadata"]["name"]))
                for lint in lints
                if lint["name"] in EXPOSURE_LINTS
                and (lint.get("metadata") or {}).get("type") in PROBEABLE_TYPES
                and lint["metadata"].get("schema")
                and lint["metadata"].get("name")
            }
        )
        return targets[: self.max_probes]

    def _findings(
        self, project_ref: str, advisors: dict[str, Any], probes: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        findings = []
        for lint in self._reportable(advisors["lints"]):
            meta = lint.get("metadata") or {}
            schema, name = meta.get("schema"), meta.get("name")
            probe = probes.get(f"{schema}.{name}")
            exposed = bool(
                probe and probe["http_status"] in (200, 206) and (probe["row_count"] or 0) > 0
            )
            findings.append(
                {
                    "fingerprint": fingerprint(project_ref, lint["name"], schema, name),
                    "finding_type": lint["name"],
                    "category": "security",
                    "severity": "critical" if exposed else LEVEL_SEVERITY[lint["level"]],
                    "source": "probe"
                    if exposed
                    else ("advisor" if lint.get("source") == "advisor" else "introspection"),
                    "object_schema": schema,
                    "object_name": name,
                    "title": lint["title"],
                    "probe_http_status": probe["http_status"] if probe else None,
                    "probe_row_count": probe["row_count"] if probe else None,
                    "probe_confirmed": exposed,
                }
            )
        return sorted(findings, key=lambda f: f["fingerprint"])

    def next_action(self, ctx: RunContext, history: Sequence[StepRecord]) -> Action:
        if ctx.connection is None:
            return Finish("failed", "no_connection")
        ref = ctx.connection.project_ref

        if not _outputs(history, "supabase.project.read"):
            return ToolCall("supabase.project.read")
        advisors_out = _outputs(history, "supabase.advisors.security")
        if not advisors_out:
            return ToolCall("supabase.advisors.security")
        advisors = advisors_out[0]

        probes = {p["table"]: p for p in _outputs(history, "probe.exposure")}
        for schema, table in self._probe_targets(advisors["lints"]):
            if f"{schema}.{table}" not in probes:
                return ToolCall("probe.exposure", {"schema": schema, "table": table})

        findings = self._findings(ref, advisors, probes)
        recorded = {
            out["fingerprint"]: out["finding_id"] for out in _outputs(history, "findings.record")
        }
        for finding in findings:
            if finding["fingerprint"] not in recorded:
                return ToolCall("findings.record", finding)

        # Postern's own catalog checks are what make a check complete (the advisor alone
        # misses some exposures). Without them, record what was found but fail the run, so
        # it never counts as a clean check (TM-16).
        done = (
            Finish("failed", "incomplete_check")
            if advisors.get("introspection_error")
            else Finish("completed")
        )
        if not findings:
            return done

        model_steps = [s for s in history if s.kind == "llm_call"]
        if not model_steps:
            return ModelCall("explain_findings", self._explain_prompt(findings))
        if not _outputs(history, "findings.explain"):
            explanations = self._parse_explanations(model_steps[-1], findings, recorded)
            if explanations:
                return ToolCall(
                    "findings.explain",
                    {
                        "explanations": explanations,
                        "model": (model_steps[-1].output or {}).get("model", "unknown"),
                    },
                )
        return done

    def _explain_prompt(self, findings: list[dict[str, Any]]) -> Prompt:
        prompt = Prompt().instruction(EXPLAIN_INSTRUCTIONS)
        for index, finding in enumerate(findings):
            prompt.instruction(f"Finding {index}: severity {finding['severity']}.")
            prompt.data(Untrusted(finding["finding_type"], "lint text"))
            if finding["object_schema"]:
                prompt.data(Untrusted(finding["object_schema"], "schema name"))
            if finding["object_name"]:
                prompt.data(Untrusted(finding["object_name"], "table name"))
            prompt.fact("anonymous visitors could read rows", finding["probe_confirmed"])
            prompt.fact(
                "rows an anonymous visitor could count (capped at 1000)", finding["probe_row_count"]
            )
        return prompt

    def _parse_explanations(
        self, step: StepRecord, findings: list[dict[str, Any]], recorded: dict[str, str]
    ) -> list[dict[str, str]]:
        if step.status != "completed" or not isinstance(step.output, dict):
            return []
        text = str(step.output.get("text", ""))
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            items = json.loads(match.group(0))
        except ValueError:
            return []
        out = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            index, body = item.get("index"), item.get("text")
            if isinstance(index, int) and 0 <= index < len(findings) and isinstance(body, str):
                out.append({"finding_id": recorded[findings[index]["fingerprint"]], "text": body})
        return out


class DrillEcho:
    """The drill agent (ADR-011): no tools; hands every run to a human."""

    name = "drill_echo"

    def next_action(self, ctx: RunContext, history: Sequence[StepRecord]) -> Action:
        return Finish("awaiting_human", "drill")
