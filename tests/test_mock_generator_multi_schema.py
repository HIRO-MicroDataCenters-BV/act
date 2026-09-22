import pulumi

from act.core.mock_generator import MockGenerator

_SHARED_CLASS_PROGRAM = """
import pulumi_cape as cape
import pulumi_kubernetes as k8s

cape.authorization.Role("platform-admin", spec={})
k8s.rbac.v1.Role("pod-reader", metadata={"name": "pod-reader"})
"""


def test_multi_schema_type_map_contains_both_providers(cape_schema_path, random_schema_path):
    mg = MockGenerator([cape_schema_path, random_schema_path])
    assert "Instance" in mg._type_map
    assert "RandomPassword" in mg._type_map


def test_class_name_shared_by_two_providers_keeps_every_token(cape_schema_path, kubernetes_schema_path):
    """CAPE and Kubernetes both declare Role; neither may displace the other."""
    mg = MockGenerator([cape_schema_path, kubernetes_schema_path])
    tokens = {info["token"] for info in mg._type_map["Role"]}
    assert "cape:authorization:Role" in tokens
    assert "kubernetes:rbac.authorization.k8s.io/v1:Role" in tokens


def test_displaced_provider_still_gets_its_defaults(cape_schema_path, kubernetes_schema_path, tmp_path):
    """The CAPE Role is registered even though Kubernetes declares Role and loads later.

    Only the CAPE side is asserted on: the Kubernetes Role declares every property as an
    input too, so it has no output-only fields to default and an empty set is correct
    for it either way.
    """
    program = tmp_path / "shared_class.py"
    program.write_text(_SHARED_CLASS_PROGRAM)

    mocks = MockGenerator([cape_schema_path, kubernetes_schema_path]).generate(str(program))()
    args = pulumi.runtime.MockResourceArgs(
        typ="cape:authorization:Role", name="r", inputs={}, provider="", resource_id=None
    )
    _, outputs = mocks.new_resource(args)

    assert outputs["status"] == "active"
    assert outputs["metadata"] == {"name": "mock-resource"}


def test_multi_schema_run_captures_both_providers(cape_schema_path, random_schema_path, multi_provider_fixtures):
    mg = MockGenerator([cape_schema_path, random_schema_path])
    result = mg.run_with_mocks(str(multi_provider_fixtures / "program.py"))
    assert "my-instance" in result
    assert "db-password" in result
