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


def test_class_name_shared_by_two_providers_keeps_both(tmp_path):
    """Two providers declaring one class name: neither entry may displace the other."""
    import json

    (tmp_path / "a.json").write_text(json.dumps({"resources": {"a:index:Bucket": {}}}))
    (tmp_path / "b.json").write_text(json.dumps({"resources": {"b:index:Bucket": {}}}))
    mg = MockGenerator([str(tmp_path / "a.json"), str(tmp_path / "b.json")])
    assert {i["token"] for i in mg._type_map["Bucket"]} == {"a:index:Bucket", "b:index:Bucket"}


def test_class_name_shared_by_api_versions_keeps_both(tmp_path):
    """One provider declaring a class once per API version keeps every version."""
    import json

    schema: dict = {"resources": {"k8s:apps/v1:Deployment": {}, "k8s:apps/v1beta1:Deployment": {}}}
    (tmp_path / "s.json").write_text(json.dumps(schema))
    mg = MockGenerator(str(tmp_path / "s.json"))
    assert {i["token"] for i in mg._type_map["Deployment"]} == {"k8s:apps/v1:Deployment", "k8s:apps/v1beta1:Deployment"}


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
