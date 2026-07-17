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
