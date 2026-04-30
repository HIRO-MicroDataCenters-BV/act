import pytest

from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.integrations.checkov_adapter import load_checkov_rules


@pytest.fixture
def k8s_oracle(kubernetes_schema_path):
    oracle = CorrectnessOracle(kubernetes_schema_path)
    load_checkov_rules(oracle, resource_type="kubernetes:apps/v1:Deployment")
    return oracle


def test_checkov_violations_detected(kubernetes_schema_path, kubernetes_fixtures, k8s_oracle):
    mg = MockGenerator(kubernetes_schema_path)
    result = mg.run_with_mocks(str(kubernetes_fixtures / "nginx_deployment_no_security.py"))
    rtype = mg.get_resource_type("nginx")
    violations = k8s_oracle.check(rtype, result["nginx"])
    assert len(violations) > 0
    assert any(v.field.startswith("CKV_") for v in violations)


def test_checkov_fewer_violations_with_security_context(kubernetes_schema_path, kubernetes_fixtures):
    mg = MockGenerator(kubernetes_schema_path)
    oracle_secure = CorrectnessOracle(kubernetes_schema_path)
    load_checkov_rules(oracle_secure, resource_type="kubernetes:apps/v1:Deployment")
    oracle_insecure = CorrectnessOracle(kubernetes_schema_path)
    load_checkov_rules(oracle_insecure, resource_type="kubernetes:apps/v1:Deployment")

    secure = mg.run_with_mocks(str(kubernetes_fixtures / "nginx_deployment.py"))
    insecure = mg.run_with_mocks(str(kubernetes_fixtures / "nginx_deployment_no_security.py"))

    rtype = "kubernetes:apps/v1:Deployment"
    v_secure = oracle_secure.check(rtype, secure["nginx"])
    v_insecure = oracle_insecure.check(rtype, insecure["nginx"])

    assert len(v_secure) < len(v_insecure)


def test_checkov_scoped_to_resource_type(kubernetes_schema_path, kubernetes_fixtures, k8s_oracle):
    mg = MockGenerator(kubernetes_schema_path)
    result = mg.run_with_mocks(str(kubernetes_fixtures / "nginx_deployment.py"))
    # Service should not trigger Deployment-scoped Checkov rules
    if "nginx-svc" in result:
        rtype = mg.get_resource_type("nginx-svc")
        violations = k8s_oracle.check(rtype, result["nginx-svc"])
        assert all(v.field.startswith("CKV_") is False or rtype == "kubernetes:apps/v1:Deployment"
                   for v in violations)


def test_invalid_provider_raises(kubernetes_schema_path):
    oracle = CorrectnessOracle(kubernetes_schema_path)
    with pytest.raises(ValueError, match="No Checkov checks found"):
        load_checkov_rules(oracle, resource_type="unknownprovider:foo:Bar")
