from act.core.mock_generator import MockGenerator


def test_type_map_loaded(cape_schema_path):
    mg = MockGenerator(cape_schema_path)
    assert "Instance" in mg._type_map
    assert "Workspace" in mg._type_map


def test_run_with_mocks_valid(cape_schema_path, cape_fixtures):
    mg = MockGenerator(cape_schema_path)
    result = mg.run_with_mocks(str(cape_fixtures / "path_a_valid.py"))
    assert "my-instance" in result
    assert result["my-instance"].get("status") == "active"


def test_security_field_absent_in_invalid(cape_schema_path, cape_fixtures):
    mg = MockGenerator(cape_schema_path)
    result = mg.run_with_mocks(str(cape_fixtures / "path_a_invalid.py"))
    assert "my-instance" in result
    outputs = result["my-instance"]
    assert "security_group_ref" not in outputs
