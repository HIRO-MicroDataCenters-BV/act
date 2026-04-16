import pytest

from act.core.mock_generator import MockGenerator


def test_random_type_map_loaded(random_schema_path):
    mg = MockGenerator(random_schema_path)
    assert "RandomPassword" in mg._type_map
    assert mg._type_map["RandomPassword"]["token"] == "random:index/randomPassword:RandomPassword"


def test_random_valid_password_captured(random_schema_path, random_fixtures):
    mg = MockGenerator(random_schema_path)
    result = mg.run_with_mocks(str(random_fixtures / "program_valid.py"))
    assert "db-password" in result
    outputs = result["db-password"]
    assert outputs["length"] == 24
    assert outputs["special"] is True


def test_random_weak_password_violation_visible(random_schema_path, random_fixtures):
    mg = MockGenerator(random_schema_path)
    result = mg.run_with_mocks(str(random_fixtures / "program_invalid.py"))
    outputs = result["db-password"]
    assert outputs["length"] == 6
    assert outputs["special"] is False
