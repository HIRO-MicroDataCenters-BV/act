import sys

import pytest

from act.core.fuzz_runner import FuzzRunner
from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.rules.cape import rule_no_exposed_instance, rule_no_unprotected_ssh


def _runner(schema_path, iterations=20):
    mg = MockGenerator(schema_path)
    oracle = CorrectnessOracle(schema_path)
    oracle.add_rule(rule_no_exposed_instance, resource_type="cape:compute:Instance")
    oracle.add_rule(rule_no_unprotected_ssh, resource_type="cape:compute:Instance")
    return FuzzRunner(mg, oracle, iterations=iterations)


def test_fuzz_runner_skips_without_atheris(cape_schema_path, path_b_fixture, monkeypatch):
    monkeypatch.setitem(sys.modules, "atheris", None)
    assert _runner(cape_schema_path).run(str(path_b_fixture)) == []


def test_fuzz_runner_skips_static_program(cape_schema_path, cape_fixtures):
    """A program with no env inputs has no Path B input space to explore."""
    pytest.importorskip("atheris")
    assert _runner(cape_schema_path).run(str(cape_fixtures / "path_a_valid.py")) == []


def test_fuzz_runner_finds_violations(cape_schema_path, path_b_fixture):
    pytest.importorskip("atheris")
    violations = _runner(cape_schema_path, iterations=50).run(str(path_b_fixture))
    assert len(violations) >= 1
    assert all(v.severity in ("HIGH", "MEDIUM", "LOW") for v in violations)


def test_fuzz_runner_reaches_ssh_without_security_group(cape_schema_path, path_b_fixture):
    """The insecure combo lives inside the nested spec, reachable only by varying env inputs."""
    pytest.importorskip("atheris")
    violations = _runner(cape_schema_path, iterations=50).run(str(path_b_fixture))
    assert any(v.field == "spec.sshKeys" for v in violations)


def test_fuzz_runner_deduplicates(cape_schema_path, path_b_fixture):
    pytest.importorskip("atheris")
    violations = _runner(cape_schema_path, iterations=200).run(str(path_b_fixture))
    keys = [(v.field, v.message) for v in violations]
    assert len(keys) == len(set(keys))
