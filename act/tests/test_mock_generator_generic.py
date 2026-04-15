import pytest

from act.core.mock_generator import MockGenerator


def test_generic_type_map_loaded(generic_schema_path):
    mg = MockGenerator(generic_schema_path)
    assert "Database" in mg._type_map
    assert mg._type_map["Database"]["token"] == "mydb:index:Database"


def test_generic_run_with_mocks(generic_schema_path, generic_fixtures):
    mg = MockGenerator(generic_schema_path)
    result = mg.run_with_mocks(str(generic_fixtures / "program_valid.py"))
    assert "prod-db" in result
    assert result["prod-db"].get("status") == "active"


def test_generic_violation_visible(generic_schema_path, generic_fixtures):
    mg = MockGenerator(generic_schema_path)
    result = mg.run_with_mocks(str(generic_fixtures / "program_invalid.py"))
    assert "prod-db" in result
    assert result["prod-db"].get("public") is True
