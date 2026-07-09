"""ACT Cognitive Validator (ACV) agent: a LangGraph planner -> tools -> synthesis loop.

Additive; never blocks the pipeline. Skips gracefully when the optional ``acv``
extra is missing, no endpoint is configured, or the endpoint is unreachable.
Enable via ``ACT_ACV_MODEL`` + ``ACT_ACV_BASE_URL`` (``CAPE_ACV_MODEL_URL`` aliases the base URL).
"""

from typing import List, Optional, Tuple, TypedDict

import logging
import time

from act.acv.models import LLM, ACVFinding, ACVResult, findings_from_tool_json, skipped_result
from act.config import DEFAULT_ACV_TIMEOUT_S, ActConfig
from act.core.mock_generator import MockGenerator

log = logging.getLogger(__name__)

try:
    import httpx
    from langgraph.graph import END, StateGraph

    from act.acv import tools as acv_tools

    _ACV_AVAILABLE = True
except ImportError:  # optional 'acv' extra not installed
    _ACV_AVAILABLE = False


_DEFAULT_TIMEOUT_S = DEFAULT_ACV_TIMEOUT_S


_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class _HttpxLLM:
    """Minimal client for an OpenAI-compatible chat-completions endpoint.

    Paces requests (``min_interval_s``) and retries rate-limit/overload responses
    (``max_retries``) so a free/quota-limited endpoint (e.g. Gemini free tier) does
    not fail the tool loop, which fires several calls in quick succession.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = _DEFAULT_TIMEOUT_S,
        api_key: Optional[str] = None,
        min_interval_s: float = 0.0,
        max_retries: int = 3,
    ):
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._timeout = timeout
        # Bearer auth for hosted endpoints (Google's OpenAI-compat, OpenAI, ...);
        # unauthenticated for a local vLLM server.
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._min_interval = max(0.0, min_interval_s)
        self._max_retries = max(0, max_retries)
        self._last_request_ts = 0.0

    def complete(self, prompt: str) -> str:
        body = {"model": self._model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
        resp = None
        for attempt in range(self._max_retries + 1):
            self._throttle()
            resp = httpx.post(self._url, json=body, headers=self._headers, timeout=self._timeout)
            if resp.status_code not in _RETRYABLE_STATUSES or attempt == self._max_retries:
                break
            delay = self._retry_delay(resp, attempt)
            log.info("acv.llm_retry status=%s attempt=%s delay=%.1fs", resp.status_code, attempt + 1, delay)
            time.sleep(delay)
        assert resp is not None  # loop runs at least once
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

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        wait = self._last_request_ts + self._min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _retry_delay(self, resp, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(2.0**attempt, 30.0)


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


def _format_oracle_context(context: Optional[dict]) -> str:
    """Render the deterministic oracle's findings as a preamble for the tool prompts."""
    violations = (context or {}).get("oracle_violations") or []
    if not violations:
        return ""
    lines = "\n".join(f"- [{v.severity}] {v.field}: {v.message}" for v in violations)
    return (
        "The deterministic oracle already reported these structural violations:\n"
        f"{lines}\n"
        "Focus on content-level or operational issues the oracle does not already cover."
    )


class ACTCognitiveValidator:
    def __init__(
        self,
        model_base_url: str,
        model_name: str,
        max_iterations: int = 3,
        client: Optional[LLM] = None,
        api_key: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
        min_request_interval_s: float = 0.0,
        max_retries: int = 3,
    ):
        """
        model_base_url: OpenAI-compatible endpoint (vLLM, OpenAI, Google's compat endpoint, ...).
        max_iterations: planner/tool cycles before returning a partial result.
        client: injected LLM client (tests pass a fake); built from base_url/model when None.
        api_key: bearer token for hosted endpoints; omit for an unauthenticated local server.
        timeout: per-request seconds; raise it for slower or reasoning models.
        min_request_interval_s: min seconds between LLM calls; pace to a free-tier RPM limit.
        max_retries: retries on rate-limit/overload (429/5xx) responses.
        """
        self._base_url = model_base_url
        self._model = model_name
        self._max_iterations = max_iterations
        self._client = client
        self._api_key = api_key
        self._timeout = timeout
        self._min_request_interval_s = min_request_interval_s
        self._max_retries = max_retries

    @classmethod
    def from_env(cls, cfg: Optional[ActConfig] = None) -> Optional["ACTCognitiveValidator"]:
        """Build from environment, or return None (pipeline then skips ACV)."""
        if not _ACV_AVAILABLE:
            log.info("acv.disabled reason=extra_not_installed")
            return None
        cfg = cfg or ActConfig.from_env()
        if not cfg.acv_model or not cfg.acv_base_url:
            log.info("acv.disabled reason=not_configured")
            return None
        return cls(
            model_base_url=cfg.acv_base_url,
            model_name=cfg.acv_model,
            api_key=cfg.acv_api_key,
            timeout=cfg.acv_timeout,
            max_iterations=cfg.acv_max_iterations,
            min_request_interval_s=cfg.acv_min_request_interval_s,
            max_retries=cfg.acv_max_retries,
        )

    def validate(self, program_path: str, context: Optional[dict] = None) -> ACVResult:
        """Run the planner-tool-synthesis loop; skip gracefully on any failure."""
        if not _ACV_AVAILABLE:
            log.warning("acv.skipped reason=extra_not_installed")
            return skipped_result()
        try:
            source = self._read_source(program_path)
            client = self._client or _HttpxLLM(
                self._base_url,
                self._model,
                timeout=self._timeout,
                api_key=self._api_key,
                min_interval_s=self._min_request_interval_s,
                max_retries=self._max_retries,
            )
            acv_tools.set_llm(client)
            acv_tools.set_oracle_context(_format_oracle_context(context))
            try:
                final = _build_graph(self._max_iterations).invoke(
                    {"program_content": source, "iterations": 0, "findings": [], "prev_signature": None}
                )
            finally:
                acv_tools.set_llm(None)
                acv_tools.set_oracle_context("")
            return _synthesise_result(final["findings"], final["iterations"])
        except Exception as exc:  # unreachable endpoint / read error / anything
            log.warning("acv.skipped reason=error err=%s", exc)
            return skipped_result()

    @staticmethod
    def _read_source(program_path: str) -> str:
        with open(MockGenerator._entry_point(program_path)) as f:
            return f.read()
