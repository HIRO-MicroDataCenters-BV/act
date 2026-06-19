import time
from unittest.mock import patch

from hypothesis import strategies as st

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
    """Discovers violations when spec or security fields are mutated to boundary values."""
    runner = _runner(cape_schema_path, max_examples=30)
    violations = runner.run(str(path_b_fixture))
    assert len(violations) >= 1
    severities = {v.severity for v in violations}
    assert "HIGH" in severities


def test_property_runner_deduplicates(cape_schema_path, path_b_fixture):
    """Same (field, message) violation pair is never returned twice."""
    runner = _runner(cape_schema_path, max_examples=50)
    violations = runner.run(str(path_b_fixture))
    keys = [(v.field, v.message) for v in violations]
    assert len(keys) == len(set(keys))


def test_property_runner_respects_max_examples(cape_schema_path, path_b_fixture):
    """Completes within a reasonable time when max_examples is small."""
    runner = _runner(cape_schema_path, max_examples=5)
    start = time.monotonic()
    runner.run(str(path_b_fixture))
    elapsed = time.monotonic() - start
    assert elapsed < 30, f"PropertyRunner took {elapsed:.1f}s with max_examples=5"


def test_property_runner_records_bad_status_type_as_violation(cape_schema_path, path_b_fixture):
    """A non-str/dict status must surface as a Violation, never crash the runner."""
    runner = _runner(cape_schema_path, max_examples=5)

    with patch(
        "act.core.property_runner.build_strategy",
        return_value=st.just({"status": 42}),
    ):
        violations = runner.run(str(path_b_fixture))

    assert any(
        v.field == "status" and "got int" in v.message for v in violations
    ), "bad status type was not recorded as a Violation"
