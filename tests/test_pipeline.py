from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.core.pipeline import ACTPipeline
from act.rules.cape import rule_no_exposed_instance, rule_no_unprotected_ssh


def _cape_pipeline(cape_schema_path):
    mg = MockGenerator(cape_schema_path)
    oracle = CorrectnessOracle(cape_schema_path)
    oracle.add_rule(rule_no_exposed_instance, resource_type="cape:compute:Instance")
    oracle.add_rule(rule_no_unprotected_ssh, resource_type="cape:compute:Instance")
    return ACTPipeline(mg, oracle)


def test_valid_program_passes(cape_schema_path, cape_fixtures):
    pipeline = _cape_pipeline(cape_schema_path)
    result = pipeline.run(str(cape_fixtures / "path_a_valid.py"))
    assert result.passed
    assert result.violations == []


def test_invalid_program_fails(cape_schema_path, cape_fixtures):
    pipeline = _cape_pipeline(cape_schema_path)
    result = pipeline.run(str(cape_fixtures / "path_a_invalid.py"))
    assert not result.passed
    assert len(result.violations) >= 1
    assert all(v.severity == "HIGH" for v in result.violations)


def test_parameterized_flag_false_for_static_program(cape_schema_path, cape_fixtures):
    pipeline = _cape_pipeline(cape_schema_path)
    result = pipeline.run(str(cape_fixtures / "path_a_valid.py"))
    assert result.parameterized is False


def test_getenv_program_is_parameterized(tmp_path):
    from act.core.pipeline import _is_parameterized

    prog = tmp_path / "p.py"
    prog.write_text("import os\nx = os.getenv('X')\n")
    assert _is_parameterized(str(prog)) is True


def test_result_carries_program_path(cape_schema_path, cape_fixtures):
    pipeline = _cape_pipeline(cape_schema_path)
    path = str(cape_fixtures / "path_a_valid.py")
    result = pipeline.run(path)
    assert result.program_path == path


def test_zero_resource_program_does_not_pass(cape_schema_path, cape_fixtures):
    pipeline = _cape_pipeline(cape_schema_path)
    result = pipeline.run(str(cape_fixtures.parent / "no_resources.py"))
    assert not result.passed
    assert result.resource_count == 0


def _parameterized_pipeline(cape_schema_path):
    from act.core.property_runner import PropertyRunner
    from act.rules import cape

    mg = MockGenerator(cape_schema_path)
    oracle = CorrectnessOracle(cape_schema_path)
    cape.register(oracle)
    return ACTPipeline(mg, oracle, property_runner=PropertyRunner(mg, oracle))


def test_plain_run_findings_name_their_resource(cape_schema_path, cape_fixtures):
    result = _cape_pipeline(cape_schema_path).run(str(cape_fixtures / "path_a_invalid.py"))
    assert [(v.resource, v.found_by) for v in result.violations] == [("my-instance", "")]


def test_input_testing_adds_only_findings_the_plain_run_missed(cape_schema_path, path_b_fixture):
    """The plain run already finds the missing security group; property testing adds only the SSH case."""
    result = _parameterized_pipeline(cape_schema_path).run(str(path_b_fixture))
    keys = [v.key() for v in result.violations]
    assert len(keys) == len(set(keys))
    by_field = {v.field: v for v in result.violations if v.resource == "my-instance"}
    assert by_field["spec.securityGroupRef"].found_by == ""
    assert by_field["spec.sshKeys"].found_by == "property testing"


def test_plain_run_findings_come_first(cape_schema_path, path_b_fixture):
    result = _parameterized_pipeline(cape_schema_path).run(str(path_b_fixture))
    order = [bool(v.found_by) for v in result.violations]
    assert order == sorted(order)


def test_shared_findings_keep_the_smallest_input(cape_schema_path, cape_fixtures):
    """Fuzzing records the first input that triggered a finding; property testing the smallest."""
    import importlib.util

    import pytest

    if importlib.util.find_spec("atheris") is None:
        pytest.skip("atheris not installed")
    from act.core.fuzz_runner import FuzzRunner
    from act.core.property_runner import PropertyRunner
    from act.rules import cape

    mg = MockGenerator(cape_schema_path)
    oracle = CorrectnessOracle(cape_schema_path)
    cape.register(oracle)
    pipeline = ACTPipeline(mg, oracle, fuzz_runner=FuzzRunner(mg, oracle), property_runner=PropertyRunner(mg, oracle))
    result = pipeline.run(str(cape_fixtures / "parameterized_secure_defaults.py"))
    assert {v.found_by for v in result.violations} == {"property testing"}
