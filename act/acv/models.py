"""Data structures for the ACT Cognitive Validator (ACV).

Dataclasses plus small adapters, dependency-free (no langgraph/langchain-core/httpx)
so the core pipeline imports these without the optional ``acv`` extra.
"""

from typing import List

import json
import logging
from dataclasses import dataclass

from act.core.violations import Violation

log = logging.getLogger(__name__)

_VALID_SEVERITIES = {"HIGH", "MEDIUM", "LOW"}


@dataclass
class ACVFinding:
    tool: str
    severity: str  # HIGH, MEDIUM, LOW
    description: str
    recommendation: str


@dataclass
class ACVResult:
    verdict: str  # PASS or FAIL
    risk_level: str  # HIGH, MEDIUM, LOW, NONE
    findings: List[ACVFinding]
    iterations: int


def skipped_result() -> ACVResult:
    """A clean, non-blocking result used whenever the validator skips."""
    return ACVResult(verdict="PASS", risk_level="NONE", findings=[], iterations=0)


def findings_from_tool_json(tool_name: str, raw: str) -> List[ACVFinding]:
    """Parse a tool's JSON output into ``ACVFinding`` objects.

    Tolerant by design: malformed or non-list JSON yields an empty list rather
    than raising, so a misbehaving analyser never breaks the run. Unrecognised
    severity falls back to ``MEDIUM``.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.debug("acv.tool_output_unparseable tool=%s", tool_name)
        return []
    if not isinstance(data, list):
        return []
    findings: List[ACVFinding] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        severity = str(entry.get("severity", "")).strip().upper()
        if severity not in _VALID_SEVERITIES:
            severity = "MEDIUM"
        findings.append(
            ACVFinding(
                tool=tool_name,
                severity=severity,
                description=_clean_text(entry.get("description")),
                recommendation=_clean_text(entry.get("recommendation")),
            )
        )
    return findings


def _clean_text(value: object) -> str:
    """Coerce a tool-supplied field to clean display text.

    Non-strings become "" so a ``"None"`` or Python ``repr`` never leaks into the report.
    """
    return value.strip() if isinstance(value, str) else ""


def acv_result_to_violations(result: ACVResult) -> List[Violation]:
    """Render ACV findings as ``Violation`` objects for the advisory report block.

    Display only: never added to the gating violation list, so ACV never changes the exit code.
    """
    violations: List[Violation] = []
    for finding in result.findings:
        message = finding.description
        if finding.recommendation:
            message = f"{message} (fix: {finding.recommendation})"
        violations.append(Violation(field=f"acv.{finding.tool}", message=message, severity=finding.severity))
    return violations
