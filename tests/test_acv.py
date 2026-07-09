"""Tests for the ACT Cognitive Validator (ACV).

Hermetic: a fake OpenAI-compatible client is injected, so no network is used.
Covers the prescribed cases - a produced finding, PASS does not block, a FAIL is
shown in the report without changing the exit code, graceful skip when the
endpoint is unreachable, and env-driven enablement.
"""

import pytest

pytest.importorskip("langgraph")

from act.acv import tools as acv_tools  # noqa: E402
from act.acv.agent import _DEFAULT_TIMEOUT_S, ACTCognitiveValidator  # noqa: E402
from act.acv.models import findings_from_tool_json  # noqa: E402
from act.core.mock_generator import MockGenerator  # noqa: E402
from act.core.oracle import CorrectnessOracle  # noqa: E402
from act.core.pipeline import ACTPipeline  # noqa: E402
from act.gate.ci_gate import CIGate  # noqa: E402
from act.rules import auto_load  # noqa: E402

FINDING_JSON = (
    '[{"severity": "HIGH", "description": "Instance exposes SSH to 0.0.0.0/0", '
    '"recommendation": "Restrict ingress to a security group"}]'
)


class FakeClient:
    """Returns a canned completion, optionally only for prompts containing `match`."""

    def __init__(self, response: str, match: str = ""):
        self.response = response
        self.match = match
        self.calls: list = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.match and self.match not in prompt:
            return "[]"
        return self.response


class RaisingClient:
    def complete(self, prompt: str) -> str:
        raise ConnectionError("connection refused")


@pytest.fixture
def valid_program(cape_fixtures):
    return str(cape_fixtures / "path_a_valid.py")


@pytest.fixture
def invalid_program(cape_fixtures):
    return str(cape_fixtures / "path_a_invalid.py")


def _validator(response: str) -> ACTCognitiveValidator:
    # Scope the canned finding to the security tool so single-finding tests stay meaningful.
    client = FakeClient(response, match="cloud security auditor")
    return ACTCognitiveValidator(model_base_url="http://fake", model_name="fake", client=client)


def _pipeline(schema_path, acv):
    mg = MockGenerator(schema_path)
    oracle = CorrectnessOracle(schema_path)
    auto_load(oracle)
    return ACTPipeline(mg, oracle, acv=acv)


# --- validate() ------------------------------------------------------------


def test_security_finding_produced(valid_program):
    result = _validator(FINDING_JSON).validate(valid_program)
    assert result.verdict == "FAIL"
    assert result.risk_level == "HIGH"
    assert len(result.findings) == 1
    assert result.findings[0].tool == "security_risk_analyser"
    # The planner/tool/synthesis loop converges after two deterministic cycles.
    assert result.iterations == 2


def test_finding_extracted_from_chatty_response(valid_program):
    wrapped = f"Here are the findings:\n```json\n{FINDING_JSON}\n```\nHope that helps."
    result = _validator(wrapped).validate(valid_program)
    assert result.verdict == "FAIL"
    assert len(result.findings) == 1


def test_clean_result_has_no_findings(valid_program):
    result = _validator("[]").validate(valid_program)
    assert result.verdict == "PASS"
    assert result.risk_level == "NONE"
    assert result.findings == []
    assert result.iterations == 1


def test_tools_return_empty_without_client():
    # With no LLM client set, every tool degrades to no findings.
    for t in acv_tools.TOOLS:
        assert t.invoke({"program_content": "anything"}) == "[]"


def test_all_tools_contribute_when_llm_responds(valid_program):
    # A client that answers every tool's prompt makes all five analysers report.
    client = FakeClient(FINDING_JSON)  # no match -> responds to all prompts
    result = ACTCognitiveValidator("http://fake", "fake", client=client).validate(valid_program)
    assert result.verdict == "FAIL"
    assert {f.tool for f in result.findings} == {t.name for t in acv_tools.TOOLS}


def test_oracle_context_reaches_tool_prompts(valid_program):
    from act.core.violations import Violation

    client = FakeClient("[]")  # no findings; we only inspect the prompts it received
    ctx = {
        "oracle_violations": [Violation(field="spec.securityGroupRef", message="no security group", severity="HIGH")]
    }
    ACTCognitiveValidator("http://fake", "fake", client=client).validate(valid_program, context=ctx)
    assert any("The deterministic oracle already reported" in p for p in client.calls)
    assert any("spec.securityGroupRef: no security group" in p for p in client.calls)
    assert acv_tools._oracle_ctx.get() == ""  # cleared after the run


# --- response robustness ---------------------------------------------------


def test_finding_survives_prose_brackets(valid_program):
    # Stray brackets in the surrounding prose must not corrupt extraction.
    chatty = "Findings [see below]: " + FINDING_JSON + " also check array[0]."
    result = _validator(chatty).validate(valid_program)
    assert result.verdict == "FAIL"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "HIGH"


def test_non_string_completion_does_not_crash(valid_program):
    # A non-string completion degrades to no findings, not an escaped exception.
    client = FakeClient(["not", "a", "string"])  # type: ignore[arg-type]
    validator = ACTCognitiveValidator("http://fake", "fake", client=client)
    result = validator.validate(valid_program)
    assert result.verdict == "PASS"
    assert result.findings == []


def test_padded_severity_not_downgraded():
    findings = findings_from_tool_json("t", '[{"severity": " high ", "description": "x"}]')
    assert findings[0].severity == "HIGH"


def test_malformed_field_values_render_empty():
    findings = findings_from_tool_json(
        "t", '[{"severity": "HIGH", "description": null, "recommendation": {"port": 22}}]'
    )
    assert findings[0].description == ""
    assert findings[0].recommendation == ""


# --- graceful skip ---------------------------------------------------------


def test_unreachable_endpoint_skips_gracefully(valid_program):
    result = ACTCognitiveValidator("http://fake", "fake", client=RaisingClient()).validate(valid_program)
    assert result.verdict == "PASS"
    assert result.findings == []


def test_read_error_skips_gracefully():
    result = _validator(FINDING_JSON).validate("/does/not/exist.py")
    assert result.verdict == "PASS"
    assert result.iterations == 0


def test_from_env_none_when_unconfigured(monkeypatch):
    for var in ("ACT_ACV_MODEL", "ACT_ACV_BASE_URL", "CAPE_ACV_MODEL_URL"):
        monkeypatch.delenv(var, raising=False)
    assert ACTCognitiveValidator.from_env() is None


def test_from_env_builds_when_configured(monkeypatch):
    monkeypatch.setenv("ACT_ACV_MODEL", "some-model")
    monkeypatch.setenv("ACT_ACV_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.delenv("CAPE_ACV_MODEL_URL", raising=False)
    acv = ACTCognitiveValidator.from_env()
    assert isinstance(acv, ACTCognitiveValidator)


def test_from_env_reads_api_key(monkeypatch):
    monkeypatch.setenv("ACT_ACV_MODEL", "some-model")
    monkeypatch.setenv("ACT_ACV_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("ACT_ACV_API_KEY", "secret")
    monkeypatch.delenv("CAPE_ACV_MODEL_URL", raising=False)
    acv = ACTCognitiveValidator.from_env()
    assert acv is not None and acv._api_key == "secret"


def test_from_env_timeout(monkeypatch):
    monkeypatch.setenv("ACT_ACV_MODEL", "m")
    monkeypatch.setenv("ACT_ACV_BASE_URL", "http://x/v1")
    monkeypatch.delenv("CAPE_ACV_MODEL_URL", raising=False)

    monkeypatch.setenv("ACT_ACV_TIMEOUT", "45")
    acv = ACTCognitiveValidator.from_env()
    assert acv is not None and acv._timeout == 45.0

    monkeypatch.setenv("ACT_ACV_TIMEOUT", "not-a-number")
    acv = ACTCognitiveValidator.from_env()
    assert acv is not None and acv._timeout == _DEFAULT_TIMEOUT_S


def test_httpx_client_sends_bearer_auth(monkeypatch):
    from act.acv import agent

    captured: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "[]"}}]}

    def _fake_post(url, json, headers, timeout):
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(agent.httpx, "post", _fake_post)
    client = agent._HttpxLLM("http://x/v1", "m", api_key="secret")
    assert client.complete("hi") == "[]"
    assert captured["headers"] == {"Authorization": "Bearer secret"}


def test_httpx_client_retries_on_rate_limit(monkeypatch):
    from act.acv import agent

    calls = {"n": 0}

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "[]"}}]}

    def _fake_post(url, json, headers, timeout):
        calls["n"] += 1
        return _Resp(429) if calls["n"] < 3 else _Resp(200)

    monkeypatch.setattr(agent.httpx, "post", _fake_post)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)  # no real backoff in the test
    client = agent._HttpxLLM("http://x/v1", "m", max_retries=3)
    assert client.complete("hi") == "[]"
    assert calls["n"] == 3  # two 429s retried, third 200 succeeds


# --- pipeline / gate integration (advisory) --------------------------------


def test_acv_pass_does_not_block(cape_schema_path, valid_program):
    gate = CIGate(_pipeline(cape_schema_path, _validator("[]")))
    assert gate.evaluate(valid_program) == 0


def test_acv_finding_shown_but_exit_code_unchanged(cape_schema_path, valid_program):
    pipeline = _pipeline(cape_schema_path, _validator(FINDING_JSON))
    gate = CIGate(pipeline)
    # Oracle is clean on the valid program, so exit stays 0 despite the ACV finding.
    assert gate.evaluate(valid_program) == 0
    report = gate.format_report(pipeline.run(valid_program))
    assert "ACV (advisory): 1 finding(s)" in report
    assert "acv.security_risk_analyser" in report


def test_acv_advisory_appended_to_failing_report(cape_schema_path, invalid_program):
    pipeline = _pipeline(cape_schema_path, _validator(FINDING_JSON))
    gate = CIGate(pipeline)
    # Oracle fails on the invalid program -> exit 1, driven by the oracle only.
    assert gate.evaluate(invalid_program) == 1
    report = gate.format_report(pipeline.run(invalid_program))
    assert report.startswith("FAIL")
    assert "ACV (advisory)" in report


def test_pipeline_runs_without_acv(cape_schema_path, valid_program):
    gate = CIGate(_pipeline(cape_schema_path, acv=None))
    assert gate.evaluate(valid_program) == 0


def test_acv_blocking_mode_gates_exit_code(cape_schema_path, valid_program):
    # Oracle passes on the valid program; in blocking mode an ACV FAIL flips the exit to 1.
    mg = MockGenerator(cape_schema_path)
    oracle = CorrectnessOracle(cape_schema_path)
    auto_load(oracle)
    pipeline = ACTPipeline(mg, oracle, acv=_validator(FINDING_JSON), acv_blocking=True)
    gate = CIGate(pipeline)
    assert gate.evaluate(valid_program) == 1
    assert "ACV (blocking): 1 finding(s)" in gate.format_report(pipeline.run(valid_program))
