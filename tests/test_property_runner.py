import time

from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.core.property_runner import PropertyRunner
from act.rules.cape import rule_no_exposed_instance, rule_no_unprotected_ssh


def _runner(schema_path, max_examples=20):
    mg = MockGenerator(schema_path)
    oracle = CorrectnessOracle(schema_path)
    oracle.add_rule(rule_no_exposed_instance, resource_type="cape:compute:Instance")
    oracle.add_rule(rule_no_unprotected_ssh, resource_type="cape:compute:Instance")
    return PropertyRunner(mg, oracle, max_examples=max_examples)


def test_property_runner_finds_violations(cape_schema_path, path_b_fixture):
    violations = _runner(cape_schema_path, max_examples=30).run(str(path_b_fixture))
    assert len(violations) >= 1
    assert "HIGH" in {v.severity for v in violations}


def test_property_runner_reaches_ssh_without_security_group(cape_schema_path, path_b_fixture):
    """SSH keys set with no security group is reachable only by re-running under varied env."""
    violations = _runner(cape_schema_path, max_examples=10).run(str(path_b_fixture))
    assert any(v.field == "spec.sshKeys" for v in violations)


def test_property_runner_skips_static_program(cape_schema_path, cape_fixtures):
    assert _runner(cape_schema_path).run(str(cape_fixtures / "path_a_valid.py")) == []


def test_property_runner_deduplicates(cape_schema_path, path_b_fixture):
    violations = _runner(cape_schema_path, max_examples=50).run(str(path_b_fixture))
    keys = [(v.field, v.message) for v in violations]
    assert len(keys) == len(set(keys))


def test_property_runner_respects_max_examples(cape_schema_path, path_b_fixture):
    runner = _runner(cape_schema_path, max_examples=5)
    start = time.monotonic()
    runner.run(str(path_b_fixture))
    elapsed = time.monotonic() - start
    assert elapsed < 30, f"PropertyRunner took {elapsed:.1f}s with max_examples=5"
