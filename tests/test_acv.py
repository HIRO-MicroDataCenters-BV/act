"""Tests for the ACT Cognitive Validator (ACV).

Hermetic: a fake OpenAI-compatible client is injected, so no network is used.
Covers the prescribed cases — a produced finding, PASS does not block, a FAIL is
shown in the report without changing the exit code, graceful skip when the
endpoint is unreachable, and env-driven enablement.
"""

import pytest

pytest.importorskip("langgraph")

from act.acv import tools as acv_tools  # noqa: E402
from act.acv.agent import ACTCognitiveValidator  # noqa: E402
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
    """Returns a canned completion and records the prompts it received."""

    def __init__(self, response: str):
        self.response = response
        self.calls: list = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
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
    return ACTCognitiveValidator(model_base_url="http://fake", model_name="fake", client=FakeClient(response))


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


def test_stub_tools_return_no_findings():
    for stub in (
        acv_tools.implementation_risk_analyser,
        acv_tools.compliance_checker,
        acv_tools.deployment_correctness_checker,
        acv_tools.resource_optimisation_checker,
    ):
        assert stub.invoke({"program_content": "anything"}) == "[]"


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
    validator = ACTCognitiveValidator("http://fake", "fake", client=FakeClient(["not", "a", "string"]))
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
