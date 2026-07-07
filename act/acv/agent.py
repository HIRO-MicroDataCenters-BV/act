"""ACT Cognitive Validator (ACV) agent: a LangGraph planner -> tools -> synthesis loop.

Additive; never blocks the pipeline. Skips gracefully when the optional ``acv``
extra is missing, no endpoint is configured, or the endpoint is unreachable.
Enable via ``ACT_ACV_MODEL`` + ``ACT_ACV_BASE_URL`` (``CAPE_ACV_MODEL_URL`` aliases the base URL).
"""

from typing import List, Optional, Tuple, TypedDict

import logging
import os

from act.acv.models import LLM, ACVFinding, ACVResult, findings_from_tool_json, skipped_result
from act.core.mock_generator import MockGenerator

log = logging.getLogger(__name__)

try:
    import httpx
    from langgraph.graph import END, StateGraph

    from act.acv import tools as acv_tools

    _ACV_AVAILABLE = True
except ImportError:  # optional 'acv' extra not installed
    _ACV_AVAILABLE = False


_DEFAULT_TIMEOUT_S = 20.0


class _HttpxLLM:
    """Minimal client for an OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = _DEFAULT_TIMEOUT_S,
        api_key: Optional[str] = None,
    ):
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._timeout = timeout
        # Bearer auth for hosted endpoints (Google's OpenAI-compat, OpenAI, ...);
        # unauthenticated for a local vLLM server.
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def complete(self, prompt: str) -> str:
        resp = httpx.post(
            self._url,
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            # A 200 can still carry an error object or empty choices; surface a clear error, not a bare KeyError.
            raise ValueError(f"unexpected chat-completions response shape: {payload!r}") from exc
        if not isinstance(content, str):
            raise ValueError(f"chat-completions content was {type(content).__name__}, expected str")
        return content


_SEVERITY_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _signature(findings: List[ACVFinding]) -> Tuple:
    return tuple(sorted((f.tool, f.severity, f.description) for f in findings))


def _dedupe(findings: List[ACVFinding]) -> List[ACVFinding]:
    seen = set()
    unique: List[ACVFinding] = []
    for f in findings:
        key = (f.tool, f.severity, f.description, f.recommendation)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _synthesise_result(findings: List[ACVFinding], iterations: int) -> ACVResult:
    if not findings:
        return ACVResult(verdict="PASS", risk_level="NONE", findings=[], iterations=iterations)
    risk = max(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 0)).severity
    return ACVResult(verdict="FAIL", risk_level=risk, findings=findings, iterations=iterations)


class _ACVState(TypedDict):
    program_content: str
    iterations: int
    findings: List[ACVFinding]
    prev_signature: Optional[Tuple]


def _planner(state: _ACVState) -> dict:
    # Count this iteration.
    return {"iterations": state["iterations"] + 1}


def _run_tools(state: _ACVState) -> dict:
    content = state["program_content"]
    findings: List[ACVFinding] = []
    for t in acv_tools.TOOLS:
        raw = t.invoke({"program_content": content})
        findings.extend(findings_from_tool_json(t.name, raw))
    return {"findings": _dedupe(findings), "prev_signature": _signature(state["findings"])}


def _synthesis(state: _ACVState) -> dict:
    # No-op node: verdict/risk are derived after the loop in _synthesise_result.
    return {}


def _make_router(max_iterations: int):
    def _route(state: _ACVState) -> str:
        findings = state["findings"]
        if not findings:
            return END
        if state["iterations"] >= max_iterations:
            return END
        # Converged: re-running the deterministic tools produced no change.
        if _signature(findings) == state["prev_signature"]:
            return END
        return "planner"

    return _route


def _build_graph(max_iterations: int):
    graph = StateGraph(_ACVState)
    graph.add_node("planner", _planner)
    graph.add_node("tools", _run_tools)
    graph.add_node("synthesis", _synthesis)
    graph.set_entry_point("planner")
    graph.add_edge("planner", "tools")
    graph.add_edge("tools", "synthesis")
    graph.add_conditional_edges("synthesis", _make_router(max_iterations), {"planner": "planner", END: END})
    return graph.compile()


class ACTCognitiveValidator:
    def __init__(
        self,
        model_base_url: str,
        model_name: str,
        max_iterations: int = 3,
        client: Optional[LLM] = None,
        api_key: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ):
        """
        model_base_url: OpenAI-compatible endpoint (vLLM, OpenAI, Google's compat endpoint, ...).
        max_iterations: planner/tool cycles before returning a partial result.
        client: injected LLM client (tests pass a fake); built from base_url/model when None.
        api_key: bearer token for hosted endpoints; omit for an unauthenticated local server.
        timeout: per-request seconds; raise it for slower or reasoning models.
        """
        self._base_url = model_base_url
        self._model = model_name
        self._max_iterations = max_iterations
        self._client = client
        self._api_key = api_key
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> Optional["ACTCognitiveValidator"]:
        """Build from environment, or return None (pipeline then skips ACV)."""
        if not _ACV_AVAILABLE:
            log.info("acv.disabled reason=extra_not_installed")
            return None
        model = os.environ.get("ACT_ACV_MODEL")
        base_url = os.environ.get("ACT_ACV_BASE_URL") or os.environ.get("CAPE_ACV_MODEL_URL")
        if not model or not base_url:
            log.info("acv.disabled reason=not_configured")
            return None
        try:
            timeout = float(os.environ.get("ACT_ACV_TIMEOUT", _DEFAULT_TIMEOUT_S))
        except ValueError:
            timeout = _DEFAULT_TIMEOUT_S
        return cls(
            model_base_url=base_url,
            model_name=model,
            api_key=os.environ.get("ACT_ACV_API_KEY"),
            timeout=timeout,
        )

    def validate(self, program_path: str, context: Optional[dict] = None) -> ACVResult:
        """Run the planner-tool-synthesis loop; skip gracefully on any failure."""
        if not _ACV_AVAILABLE:
            log.warning("acv.skipped reason=extra_not_installed")
            return skipped_result()
        try:
            source = self._read_source(program_path)
            client = self._client or _HttpxLLM(
                self._base_url, self._model, timeout=self._timeout, api_key=self._api_key
            )
            acv_tools.set_llm(client)
            try:
                final = _build_graph(self._max_iterations).invoke(
                    {"program_content": source, "iterations": 0, "findings": [], "prev_signature": None}
                )
            finally:
                acv_tools.set_llm(None)
            return _synthesise_result(final["findings"], final["iterations"])
        except Exception as exc:  # unreachable endpoint / read error / anything
            log.warning("acv.skipped reason=error err=%s", exc)
            return skipped_result()

    @staticmethod
    def _read_source(program_path: str) -> str:
        with open(MockGenerator._entry_point(program_path)) as f:
            return f.read()
