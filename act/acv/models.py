"""Data structures for the ACT Cognitive Validator (ACV).

Pure dataclasses plus small adapters. Deliberately free of heavy dependencies
(no langgraph / langchain-core / httpx) so the core pipeline can import these
types without pulling in the optional ``acv`` extra.
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

    Tolerant by design: malformed JSON, a non-list payload, or entries missing
    fields yield an empty list rather than raising — a misbehaving analyser must
    never break the run. An unrecognised severity falls back to ``MEDIUM``.
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
        severity = str(entry.get("severity", "")).upper()
        if severity not in _VALID_SEVERITIES:
            severity = "MEDIUM"
        findings.append(
            ACVFinding(
                tool=tool_name,
                severity=severity,
                description=str(entry.get("description", "")).strip(),
                recommendation=str(entry.get("recommendation", "")).strip(),
            )
        )
    return findings


def acv_result_to_violations(result: ACVResult) -> List[Violation]:
    """Render ACV findings as ``Violation`` objects for the advisory report block.

    Display only: the pipeline never adds these to its gating violation list, so
    ACV findings do not change the exit code.
    """
    violations: List[Violation] = []
    for finding in result.findings:
        message = finding.description
        if finding.recommendation:
            message = f"{message} (fix: {finding.recommendation})"
        violations.append(Violation(field=f"acv.{finding.tool}", message=message, severity=finding.severity))
    return violations
