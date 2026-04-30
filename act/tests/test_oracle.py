import json
import pytest

from act.core.cape_rules import rule_no_exposed_instance, rule_no_unprotected_ssh
from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle, Violation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_schema(tmp_path, input_props: dict, required: list = None) -> str:
    """Write a minimal provider schema with the given inputProperties and return its path."""
    schema = {
        "resources": {
            "test:index:Resource": {
                "inputProperties": input_props,
                "requiredInputs": required or [],
            }
        }
    }
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema))
    return str(path)


RTYPE = "test:index:Resource"


# ---------------------------------------------------------------------------
# Engine tests — synthetic inputs, no provider fixtures, no MockGenerator
# ---------------------------------------------------------------------------

def test_required_field_missing_flagged(cape_schema_path):
    oracle = CorrectnessOracle(cape_schema_path)
    violations = oracle.check("cape:compute:Instance", {})
    assert any(v.field == "spec" and v.severity == "HIGH" for v in violations)


def test_no_violations_when_required_fields_present(cape_schema_path):
    oracle = CorrectnessOracle(cape_schema_path)
    violations = oracle.check("cape:compute:Instance", {"spec": {}})
    assert violations == []


def test_custom_rule_plugged_and_fires(cape_schema_path):
    oracle = CorrectnessOracle(cape_schema_path)
    oracle.add_rule(
        lambda inputs: [Violation("x", "test rule", "LOW")] if not inputs.get("x") else []
    )
    violations = oracle.check("cape:compute:Instance", {})
    assert any(v.field == "x" and v.severity == "LOW" for v in violations)


def test_custom_rule_does_not_fire_when_condition_met(cape_schema_path):
    oracle = CorrectnessOracle(cape_schema_path)
    oracle.add_rule(
        lambda inputs: [Violation("x", "test rule", "LOW")] if not inputs.get("x") else []
    )
    violations = oracle.check("cape:compute:Instance", {"spec": {}, "x": "present"})
    assert not any(v.field == "x" for v in violations)


def test_unknown_resource_type_returns_empty(cape_schema_path):
    oracle = CorrectnessOracle(cape_schema_path)
    violations = oracle.check("cape:unknown:Thing", {"foo": "bar"})
    assert violations == []


def test_multiple_rules_combined(cape_schema_path):
    oracle = CorrectnessOracle(cape_schema_path)
    oracle.add_rule(lambda inputs: [Violation("a", "rule a", "LOW")])
    oracle.add_rule(lambda inputs: [Violation("b", "rule b", "MEDIUM")])
    violations = oracle.check("cape:compute:Instance", {"spec": {}})
    fields = {v.field for v in violations}
    assert "a" in fields and "b" in fields


# ---------------------------------------------------------------------------
# CAPE rule tests — use MockGenerator + existing fixtures
# ---------------------------------------------------------------------------

def test_valid_instance_no_violations(cape_schema_path, cape_fixtures):
    mg = MockGenerator(cape_schema_path)
    result = mg.run_with_mocks(str(cape_fixtures / "path_a_valid.py"))
    oracle = CorrectnessOracle(cape_schema_path)
    oracle.add_rule(rule_no_exposed_instance, resource_type="cape:compute:Instance")
    oracle.add_rule(rule_no_unprotected_ssh, resource_type="cape:compute:Instance")
    violations = oracle.check("cape:compute:Instance", result["my-instance"])
    assert violations == []


def test_invalid_instance_violations_detected(cape_schema_path, cape_fixtures):
    mg = MockGenerator(cape_schema_path)
    result = mg.run_with_mocks(str(cape_fixtures / "path_a_invalid.py"))
    oracle = CorrectnessOracle(cape_schema_path)
    oracle.add_rule(rule_no_exposed_instance, resource_type="cape:compute:Instance")
    oracle.add_rule(rule_no_unprotected_ssh, resource_type="cape:compute:Instance")
    violations = oracle.check("cape:compute:Instance", result["my-instance"])
    assert len(violations) >= 1
    assert all(v.severity == "HIGH" for v in violations)


def test_missing_security_group_flagged_by_correct_rule(cape_schema_path, cape_fixtures):
    mg = MockGenerator(cape_schema_path)
    result = mg.run_with_mocks(str(cape_fixtures / "path_a_invalid.py"))
    oracle = CorrectnessOracle(cape_schema_path)
    oracle.add_rule(rule_no_exposed_instance, resource_type="cape:compute:Instance")
    violations = oracle.check("cape:compute:Instance", result["my-instance"])
    assert any(v.field == "spec.securityGroupRef" for v in violations)


def test_instance_rules_do_not_fire_on_workspace(cape_schema_path, cape_fixtures):
    mg = MockGenerator(cape_schema_path)
    result = mg.run_with_mocks(str(cape_fixtures / "path_a_valid.py"))
    oracle = CorrectnessOracle(cape_schema_path)
    # Instance rules registered with type scope — must not fire on Workspace
    oracle.add_rule(rule_no_exposed_instance, resource_type="cape:compute:Instance")
    oracle.add_rule(rule_no_unprotected_ssh, resource_type="cape:compute:Instance")
    violations = oracle.check("cape:workspace:Workspace", result["my-workspace"])
    assert violations == []


# ---------------------------------------------------------------------------
# Range and enum inference tests — synthetic schema via tmp_path
# ---------------------------------------------------------------------------

def test_minimum_violation(tmp_path):
    schema_path = _make_schema(tmp_path, {"cpu": {"type": "integer", "minimum": 1}})
    oracle = CorrectnessOracle(schema_path)
    violations = oracle.check(RTYPE, {"cpu": 0})
    assert any(v.field == "cpu" and v.severity == "HIGH" for v in violations)


def test_minimum_passes(tmp_path):
    schema_path = _make_schema(tmp_path, {"cpu": {"type": "integer", "minimum": 1}})
    oracle = CorrectnessOracle(schema_path)
    violations = oracle.check(RTYPE, {"cpu": 1})
    assert violations == []


def test_maximum_violation(tmp_path):
    schema_path = _make_schema(tmp_path, {"cpu": {"type": "integer", "maximum": 64}})
    oracle = CorrectnessOracle(schema_path)
    violations = oracle.check(RTYPE, {"cpu": 99999})
    assert any(v.field == "cpu" and v.severity == "HIGH" for v in violations)


def test_maximum_passes(tmp_path):
    schema_path = _make_schema(tmp_path, {"cpu": {"type": "integer", "maximum": 64}})
    oracle = CorrectnessOracle(schema_path)
    violations = oracle.check(RTYPE, {"cpu": 64})
    assert violations == []


def test_enum_violation(tmp_path):
    schema_path = _make_schema(tmp_path, {"arch": {"type": "string", "enum": ["x86", "arm", "riscv"]}})
    oracle = CorrectnessOracle(schema_path)
    violations = oracle.check(RTYPE, {"arch": "fpga"})
    assert any(v.field == "arch" and v.severity == "HIGH" for v in violations)


def test_enum_passes(tmp_path):
    schema_path = _make_schema(tmp_path, {"arch": {"type": "string", "enum": ["x86", "arm", "riscv"]}})
    oracle = CorrectnessOracle(schema_path)
    violations = oracle.check(RTYPE, {"arch": "riscv"})
    assert violations == []


def test_type_error_skips_range_check(tmp_path):
    schema_path = _make_schema(tmp_path, {"cpu": {"type": "integer", "minimum": 1, "maximum": 64}})
    oracle = CorrectnessOracle(schema_path)
    violations = oracle.check(RTYPE, {"cpu": "eight"})  # wrong type
    fields = [v.field for v in violations]
    assert fields.count("cpu") == 1  # type error reported once, range not double-counted
