"""LangChain tools for the ACT Cognitive Validator.

Each tool takes the Pulumi program source and returns findings as a JSON string
(``[{"severity": ..., "description": ..., "recommendation": ...}, ...]``).
Only ``security_risk_analyser`` is substantive (queries the LLM); the other four
are stubs returning no findings. The LLM is injected via :func:`set_llm` so tools
stay hermetic under test.
"""

from typing import Optional, Protocol

import contextvars
import json
import logging

from langchain_core.tools import tool

log = logging.getLogger(__name__)


class LLM(Protocol):
    def complete(self, prompt: str) -> str:
        """Return the model's text completion for a prompt."""
        ...


# ContextVar (not a global) so concurrent validations on separate threads don't race on the client.
_llm: "contextvars.ContextVar[Optional[LLM]]" = contextvars.ContextVar("acv_llm", default=None)


def set_llm(client: Optional[LLM]) -> None:
    """Install (or clear) the LLM client the substantive tools call."""
    _llm.set(client)


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
    """Return the first substring that decodes as a JSON list, else ``"[]"``.

    Scans each ``[`` so stray brackets/trailing prose don't break a naive
    first-``[``-to-last-``]`` slice.
    """
    if not isinstance(text, str) or not text:
        return "[]"
    decoder = json.JSONDecoder()
    idx = text.find("[")
    while idx != -1:
        try:
            value, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            idx = text.find("[", idx + 1)
            continue
        if isinstance(value, list):
            return text[idx:end]
        idx = text.find("[", idx + 1)
    return "[]"


@tool
def security_risk_analyser(program_content: str) -> str:
    """Check open ports, RBAC, API auth, EU sovereignty; return findings as JSON."""
    client = _llm.get()
    if client is None:
        return "[]"
    try:
        raw = client.complete(_SECURITY_PROMPT.format(program=program_content))
        # Extraction stays in the try so a malformed completion degrades to "[]" instead of escaping the graph.
        return _extract_json_array(raw)
    except Exception as exc:  # network / endpoint / decode: never break the graph
        log.warning("acv.tool_llm_error tool=security_risk_analyser err=%s", exc)
        return "[]"


@tool
def implementation_risk_analyser(program_content: str) -> str:
    """Check dependency ordering, undefined outputs, hardcoded secrets, arch mismatch."""
    # Stub: returns no findings.
    return "[]"


@tool
def compliance_checker(program_content: str) -> str:
    """Check CAPE operational policy beyond what schema covers."""
    # Stub: returns no findings.
    return "[]"


@tool
def deployment_correctness_checker(program_content: str) -> str:
    """Check resource completeness, pinned provider versions, target reachability."""
    # Stub: returns no findings.
    return "[]"


@tool
def resource_optimisation_checker(program_content: str) -> str:
    """Check over/under-provisioning and redundant resources."""
    # Stub: returns no findings.
    return "[]"


TOOLS = [
    security_risk_analyser,
    implementation_risk_analyser,
    compliance_checker,
    deployment_correctness_checker,
    resource_optimisation_checker,
]
