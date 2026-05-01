
from act.core.mock_generator import MockGenerator


def test_kubernetes_type_map_loaded(kubernetes_schema_path):
    mg = MockGenerator(kubernetes_schema_path)
    assert "Deployment" in mg._type_map
    assert "Deployment" in mg._type_map["Deployment"]["token"]


def test_kubernetes_deployment_captured(kubernetes_schema_path, kubernetes_fixtures):
    mg = MockGenerator(kubernetes_schema_path)
    result = mg.run_with_mocks(str(kubernetes_fixtures / "nginx_deployment.py"))
    assert "nginx" in result
    outputs = result["nginx"]
    assert outputs["spec"]["replicas"] == 2
    assert outputs["spec"]["template"]["spec"]["containers"][0]["name"] == "nginx"


def test_kubernetes_security_context_present(kubernetes_schema_path, kubernetes_fixtures):
    mg = MockGenerator(kubernetes_schema_path)
    result = mg.run_with_mocks(str(kubernetes_fixtures / "nginx_deployment.py"))
    container = result["nginx"]["spec"]["template"]["spec"]["containers"][0]
    assert container.get("securityContext", {}).get("runAsNonRoot") is True


def test_kubernetes_security_context_absent(kubernetes_schema_path, kubernetes_fixtures):
    mg = MockGenerator(kubernetes_schema_path)
    result = mg.run_with_mocks(str(kubernetes_fixtures / "nginx_deployment_no_security.py"))
    container = result["nginx"]["spec"]["template"]["spec"]["containers"][0]
    assert "securityContext" not in container
