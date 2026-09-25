from unittest.mock import MagicMock

from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.core.pipeline import ACTPipeline
from act.gate.ci_gate import CIGate
from act.rules.cape import rule_no_exposed_instance, rule_no_unprotected_ssh


def _gate(cape_schema_path):
    mg = MockGenerator(cape_schema_path)
    oracle = CorrectnessOracle(cape_schema_path)
    oracle.add_rule(rule_no_exposed_instance, resource_type="cape:compute:Instance")
    oracle.add_rule(rule_no_unprotected_ssh, resource_type="cape:compute:Instance")
    return CIGate(ACTPipeline(mg, oracle))


def test_exit_0_on_valid_program(cape_schema_path, cape_fixtures, capsys):
    gate = _gate(cape_schema_path)
    code = gate.evaluate(str(cape_fixtures / "path_a_valid.py"))
    assert code == 0
    assert "PASS" in capsys.readouterr().out


def test_exit_1_on_invalid_program(cape_schema_path, cape_fixtures, capsys):
    gate = _gate(cape_schema_path)
    code = gate.evaluate(str(cape_fixtures / "path_a_invalid.py"))
    assert code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "HIGH" in out


def test_exit_2_on_missing_program(cape_schema_path):
    gate = _gate(cape_schema_path)
    code = gate.evaluate("/nonexistent/path.py")
    assert code == 2


def test_exit_2_on_zero_resources(cape_schema_path, cape_fixtures, capsys):
    gate = _gate(cape_schema_path)
    code = gate.evaluate(str(cape_fixtures.parent / "no_resources.py"))
    assert code == 2
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "no resources captured" in out


def test_unexpected_error_writes_traceback_to_stderr(capsys):
    """Unexpected pipeline errors emit the message + a full traceback on stderr (not stdout)."""
    pipeline = MagicMock()
    pipeline.run.side_effect = RuntimeError("boom")

    code = CIGate(pipeline).evaluate("some_program.py")

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "[ERROR] Pipeline failed: boom" in captured.err
    assert "Traceback (most recent call last):" in captured.err
    assert "RuntimeError: boom" in captured.err


def test_report_names_the_resource_and_the_triggering_input():
    from act.core.pipeline import PipelineResult
    from act.core.violations import Violation

    plain = Violation("spec.securityGroupRef", "no security group", "HIGH", resource="my-instance")
    found = Violation(
        "spec.sshKeys",
        "SSH is open",
        "HIGH",
        resource="web-vm",
        found_by="property testing",
        inputs={"CAPE_ZONE": None, "CAPE_SECURITY_GROUP_REF": "", "CAPE_SSH_KEYS": None},
    )
    result = PipelineResult(
        passed=False, violations=[plain, found], program_path="p.py", parameterized=True, resource_count=2
    )
    assert CIGate(MagicMock()).format_report(result).splitlines() == [
        "FAIL  p.py",
        "  [HIGH] spec.securityGroupRef on my-instance: no security group",
        "  [HIGH] spec.sshKeys on web-vm: SSH is open",
        '      found by property testing with CAPE_SECURITY_GROUP_REF="" (CAPE_ZONE, CAPE_SSH_KEYS unset)',
    ]
