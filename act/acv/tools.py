"""LangChain tools for the ACT Cognitive Validator.

Each tool takes the Pulumi program source and returns findings as a JSON string
(``[{"severity": ..., "description": ..., "recommendation": ...}, ...]``).

Only ``security_risk_analyser`` is substantive today: it queries the configured
LLM. The other four are prototype stubs that return no findings — they are wired
into the graph so the agent shape is complete, and will be substantiated later.

The LLM is injected via :func:`set_llm` so the tools stay hermetic under test
(no network, no import-time client).
"""

from typing import Optional, Protocol

import logging

from langchain_core.tools import tool

log = logging.getLogger(__name__)


class LLM(Protocol):
    def complete(self, prompt: str) -> str:
        """Return the model's text completion for a prompt."""
        ...


_llm: Optional[LLM] = None


def set_llm(client: Optional[LLM]) -> None:
    """Install (or clear) the LLM client the substantive tools call."""
    global _llm
    _llm = client


_SECURITY_PROMPT = """You are a cloud security auditor reviewing a Pulumi infrastructure program.
Inspect the program for security risks in these categories:
- open or overly-permissive network ports and security groups
- missing or overly-broad RBAC / access control
- missing API authentication
- EU data-sovereignty compliance (region and data residency)

Respond with ONLY a JSON array, no prose. Each element must be an object:
{{"severity": "HIGH|MEDIUM|LOW", "description": "<what is wrong>", "recommendation": "<how to fix>"}}
Return [] if there are no findings.

PROGRAM:
{program}
"""


def _extract_json_array(text: str) -> str:
    """Pull the first ``[...]`` JSON array out of a (possibly chatty) response."""
    if not text:
        return "[]"
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return "[]"
    return text[start : end + 1]


@tool
def security_risk_analyser(program_content: str) -> str:
    """
    Checks: open ports, RBAC presence, API auth, EU sovereignty compliance.
    Returns findings as JSON string.
    """
    if _llm is None:
        return "[]"
    try:
        raw = _llm.complete(_SECURITY_PROMPT.format(program=program_content))
    except Exception as exc:  # network / endpoint / decode — never break the graph
        log.warning("acv.tool_llm_error tool=security_risk_analyser err=%s", exc)
        return "[]"
    return _extract_json_array(raw)


@tool
def implementation_risk_analyser(program_content: str) -> str:
    """
    Checks: dependency ordering, undefined outputs, hardcoded secrets,
    architecture mismatch.
    Returns findings as JSON string.
    """
    # Prototype stub — not yet implemented. Returns no findings.
    return "[]"


@tool
def compliance_checker(program_content: str) -> str:
    """
    Checks: CAPE operational policy adherence beyond what schema covers.
    Returns findings as JSON string.
    """
    # Prototype stub — not yet implemented. Returns no findings.
    return "[]"


@tool
def deployment_correctness_checker(program_content: str) -> str:
    """
    Checks: resource definitions complete, provider versions pinned,
    target architecture reachable.
    Returns findings as JSON string.
    """
    # Prototype stub — not yet implemented. Returns no findings.
    return "[]"


@tool
def resource_optimisation_checker(program_content: str) -> str:
    """
    Checks: over/under-provisioning, redundant resources, energy flags.
    Returns findings as JSON string.
    """
    # Prototype stub — not yet implemented. Returns no findings.
    return "[]"


TOOLS = [
    security_risk_analyser,
    implementation_risk_analyser,
    compliance_checker,
    deployment_correctness_checker,
    resource_optimisation_checker,
]
