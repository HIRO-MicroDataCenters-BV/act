from act.core.mock_generator import MockGenerator


def test_multi_schema_type_map_contains_both_providers(cape_schema_path, random_schema_path):
    mg = MockGenerator([cape_schema_path, random_schema_path])
    assert "Instance" in mg._type_map
    assert "RandomPassword" in mg._type_map


def test_multi_schema_run_captures_both_providers(
    cape_schema_path, random_schema_path, multi_provider_fixtures
):
    mg = MockGenerator([cape_schema_path, random_schema_path])
    result = mg.run_with_mocks(str(multi_provider_fixtures / "program.py"))
    assert "my-instance" in result
    assert "db-password" in result
