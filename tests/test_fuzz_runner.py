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


def test_fuzz_runner_skips_without_atheris(cape_schema_path, cape_fixtures, monkeypatch):
    """Returns [] when atheris is not importable."""
    monkeypatch.setitem(sys.modules, "atheris", None)
    runner = _runner(cape_schema_path)
    result = runner.run(str(cape_fixtures / "path_a_valid.py"))
    assert result == []


def test_fuzz_runner_finds_violations(cape_schema_path, path_b_fixture):
    """Finds boundary violations via atheris mutation (Linux only)."""
    pytest.importorskip("atheris")
    runner = _runner(cape_schema_path, iterations=50)
    violations = runner.run(str(path_b_fixture))
    assert len(violations) >= 1
    assert all(v.severity in ("HIGH", "MEDIUM", "LOW") for v in violations)


def test_fuzz_runner_deduplicates(cape_schema_path, path_b_fixture):
    """Violations are deduplicated — same (field, message) pair appears once."""
    pytest.importorskip("atheris")
    runner = _runner(cape_schema_path, iterations=200)
    violations = runner.run(str(path_b_fixture))
    keys = [(v.field, v.message) for v in violations]
    assert len(keys) == len(set(keys))
