"""LangChain tools for the ACT Cognitive Validator.

Each tool takes the Pulumi program source and returns findings as a JSON string
(``[{"severity": ..., "description": ..., "recommendation": ...}, ...]``).
Every tool queries the configured LLM for its own risk domain via :func:`_run_llm_tool`.
The LLM is injected via :func:`set_llm` so tools stay hermetic under test (no client
set -> each returns ``"[]"``).
"""

from typing import Optional

import contextvars
import json
import logging

from langchain_core.tools import tool

from act.acv.models import LLM

log = logging.getLogger(__name__)


# ContextVar (not a global) so concurrent validations on separate threads don't race on the client.
_llm: "contextvars.ContextVar[Optional[LLM]]" = contextvars.ContextVar("acv_llm", default=None)


def set_llm(client: Optional[LLM]) -> None:
    """Install (or clear) the LLM client the substantive tools call."""
    _llm.set(client)


# Deterministic-oracle findings, prepended to every tool prompt so the analysers focus
# on what the oracle cannot already catch. ContextVar for the same thread-safety reason.
_oracle_ctx: "contextvars.ContextVar[str]" = contextvars.ContextVar("acv_oracle_ctx", default="")


def set_oracle_context(text: str) -> None:
    """Install (or clear) the oracle-findings preamble shared with every tool prompt."""
    _oracle_ctx.set(text or "")


# Shared output contract appended to every tool prompt (doubled braces survive str.format).
_JSON_CONTRACT = """

Respond with ONLY a JSON array, no prose. Each element must be an object:
{{"severity": "HIGH|MEDIUM|LOW", "description": "<what is wrong>", "recommendation": "<how to fix>"}}
Return [] if there are no findings.

PROGRAM:
{program}
"""

_SECURITY_PROMPT = """You are a cloud security auditor reviewing a Pulumi infrastructure program.
Inspect the program for security risks in these categories:
- open or overly-permissive network ports and security groups
- missing or overly-broad RBAC / access control
- missing API authentication
- EU data-sovereignty compliance (region and data residency)""" + _JSON_CONTRACT

_IMPLEMENTATION_PROMPT = """You are an infrastructure-code reviewer inspecting a Pulumi program for
implementation risks that surface at deployment time.
Inspect for:
- resource dependency ordering the program does not correctly express (missing parent/depends_on, implicit ordering)
- outputs referenced before they are defined, or exports of undefined values
- hardcoded secrets, credentials, or tokens embedded in the source
- architecture mismatches (image/arch/nodeSelector that cannot schedule on the target)""" + _JSON_CONTRACT

_COMPLIANCE_PROMPT = (
    """You are a compliance reviewer checking a Pulumi program against CAPE
operational policy the schema alone cannot enforce.
Inspect for:
- default or shared resources used where a dedicated, least-privilege resource is required
- missing required labels, metadata, tenancy, or workspace scoping
- operational-policy violations (naming, region/zone policy, data retention) beyond structural schema checks"""
    + _JSON_CONTRACT
)

_DEPLOYMENT_PROMPT = (
    """You are a deployment-correctness reviewer checking whether a Pulumi program's
intended outcome is achievable on the target platform.
Inspect for:
- incomplete resource definitions (fields required for the resource to actually deploy are absent)
- unpinned or floating provider/image versions that make deployments non-reproducible
- targets that may be unreachable or unschedulable (nonexistent zone/region/node, missing prerequisites)"""
    + _JSON_CONTRACT
)

_OPTIMISATION_PROMPT = """You are a resource-optimisation reviewer checking a Pulumi program for
inefficient but technically-valid configuration.
Inspect for:
- over-provisioning (requests/limits, replica counts, or instance sizes larger than needed)
- under-provisioning likely to cause instability
- redundant or duplicate resources, and configuration with avoidable energy or cost overhead""" + _JSON_CONTRACT


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


def _run_llm_tool(tool_name: str, prompt: str, program_content: str) -> str:
    """Format the prompt, call the LLM, and extract the JSON array of findings.

    Returns "[]" when no client is set or the call fails, so one tool never breaks the graph.
    """
    client = _llm.get()
    if client is None:
        return "[]"
    try:
        full = prompt.format(program=program_content)
        oracle_ctx = _oracle_ctx.get()
        if oracle_ctx:
            full = oracle_ctx + "\n\n" + full
        return _extract_json_array(client.complete(full))
    except Exception as exc:  # network / endpoint / decode: never break the graph
        log.warning("acv.tool_llm_error tool=%s err=%s", tool_name, exc)
        return "[]"


@tool
def security_risk_analyser(program_content: str) -> str:
    """Check open ports, RBAC, API auth, EU sovereignty; return findings as JSON."""
    return _run_llm_tool("security_risk_analyser", _SECURITY_PROMPT, program_content)


@tool
def implementation_risk_analyser(program_content: str) -> str:
    """Check dependency ordering, undefined outputs, hardcoded secrets, arch mismatch."""
    return _run_llm_tool("implementation_risk_analyser", _IMPLEMENTATION_PROMPT, program_content)


@tool
def compliance_checker(program_content: str) -> str:
    """Check CAPE operational policy beyond what the schema covers."""
    return _run_llm_tool("compliance_checker", _COMPLIANCE_PROMPT, program_content)


@tool
def deployment_correctness_checker(program_content: str) -> str:
    """Check resource completeness, pinned provider versions, target reachability."""
    return _run_llm_tool("deployment_correctness_checker", _DEPLOYMENT_PROMPT, program_content)


@tool
def resource_optimisation_checker(program_content: str) -> str:
    """Check over/under-provisioning, redundant resources, energy/cost overhead."""
    return _run_llm_tool("resource_optimisation_checker", _OPTIMISATION_PROMPT, program_content)


TOOLS = [
    security_risk_analyser,
    implementation_risk_analyser,
    compliance_checker,
    deployment_correctness_checker,
    resource_optimisation_checker,
]
