import pytest

from act.core.mock_generator import MockGenerator


def test_run_with_mocks_times_out_on_slow_program(cape_schema_path, tmp_path):
    prog = tmp_path / "slow.py"
    prog.write_text("import time\ntime.sleep(5)\n")
    mg = MockGenerator(cape_schema_path, exec_timeout_s=1)
    with pytest.raises(TimeoutError):
        mg.run_with_mocks(str(prog))


def test_type_map_loaded(cape_schema_path):
    mg = MockGenerator(cape_schema_path)
    assert "Instance" in mg._type_map
    assert "Workspace" in mg._type_map


def test_detects_aliased_import(cape_schema_path, tmp_path):
    prog = tmp_path / "aliased.py"
    prog.write_text("from pulumi_cape.compute import Instance as VM\nVM('x', spec={}, workspace='w')\n")
    mg = MockGenerator(cape_schema_path)
    assert "Instance" in mg._detect_resource_types(str(prog))


def test_warns_on_class_name_collision(tmp_path, caplog):
    import json
    import logging

    (tmp_path / "a.json").write_text(json.dumps({"resources": {"a:index:Bucket": {}}}))
    (tmp_path / "b.json").write_text(json.dumps({"resources": {"b:index:Bucket": {}}}))
    with caplog.at_level(logging.WARNING, logger="act"):
        MockGenerator([str(tmp_path / "a.json"), str(tmp_path / "b.json")])
    assert "class_name_collision" in caplog.text


def test_no_collision_warning_same_provider_versions(tmp_path, caplog):
    import json
    import logging

    # Same class across a provider's own API versions is expected, not a collision.
    schema: dict = {"resources": {"k8s:apps/v1:Deployment": {}, "k8s:apps/v1beta1:Deployment": {}}}
    (tmp_path / "s.json").write_text(json.dumps(schema))
    with caplog.at_level(logging.WARNING, logger="act"):
        MockGenerator(str(tmp_path / "s.json"))
    assert "class_name_collision" not in caplog.text


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
