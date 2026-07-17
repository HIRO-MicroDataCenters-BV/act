import os

import pytest

from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.integrations.checkov_adapter import load_checkov_rules


def test_run_checkov_cleans_temp_on_dump_failure(monkeypatch, tmp_path):
    import act.integrations.checkov_adapter as ca

    created: list[str] = []
    real_mkstemp = ca.tempfile.mkstemp

    def _tracked_mkstemp(*a, **k):
        fd, path = real_mkstemp(dir=tmp_path, suffix=".yaml")
        created.append(path)
        return fd, path

    def _boom(*a, **k):
        raise RuntimeError("dump failed")

    monkeypatch.setattr(ca.tempfile, "mkstemp", _tracked_mkstemp)
    monkeypatch.setattr(ca.yaml, "dump", _boom)

    with pytest.raises(RuntimeError):
        ca._run_checkov("kubernetes", {"kind": "Deployment"})
    assert created and not any(os.path.exists(p) for p in created)


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
        assert all(v.field.startswith("CKV_") is False or rtype == "kubernetes:apps/v1:Deployment" for v in violations)


def test_invalid_provider_raises(kubernetes_schema_path):
    oracle = CorrectnessOracle(kubernetes_schema_path)
    with pytest.raises(ValueError, match="No Checkov checks found"):
        load_checkov_rules(oracle, resource_type="unknownprovider:foo:Bar")


def test_run_load_extra_rules_logs_when_checkov_skips_provider(caplog):
    """A provider without Checkov coverage is skipped, but the skip is logged."""
    import logging
    from unittest.mock import MagicMock

    from act.run import _load_extra_rules

    mg = MagicMock()
    mg._type_map = {"Thing": {"token": "unknownprov:foo:Thing"}}

    with caplog.at_level(logging.DEBUG, logger="act"):
        _load_extra_rules(oracle=MagicMock(), mg=mg, engines=["checkov"])

    assert any("checkov.skipped_provider" in rec.message or "skipped_provider" in rec.message for rec in caplog.records)
