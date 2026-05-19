from unittest.mock import MagicMock

from act.reproducibility.runtime_check import (
    RuntimeCheckFailure,
    RuntimeCheckResult,
    extract_target_spec,
)
from act.reproducibility.substrates.base import TargetSpec


def _mg_returning_types(types: dict[str, str]) -> MagicMock:
    mg = MagicMock()
    mg.get_resource_type.side_effect = lambda name: types.get(name)
    return mg


def test_spec_arch_from_k8s_node_selector():
    plan = {
        "nginx": {
            "spec": {
                "template": {
                    "spec": {
                        "nodeSelector": {"kubernetes.io/arch": "amd64"},
                        "containers": [{"name": "nginx", "image": "nginx:1.25"}],
                    }
                }
            }
        }
    }
    mg = _mg_returning_types({"nginx": "kubernetes:apps/v1:Deployment"})

    spec = extract_target_spec(plan, mg)

    assert spec.arch == "x86_64-linux"
    assert spec.orchestrator == "k8s"


def test_spec_arch_riscv64_from_node_selector():
    plan = {
        "nginx": {
            "spec": {
                "template": {
                    "spec": {
                        "nodeSelector": {"kubernetes.io/arch": "riscv64"},
                        "containers": [{"name": "nginx", "image": "nginx:1.25"}],
                    }
                }
            }
        }
    }
    mg = _mg_returning_types({"nginx": "kubernetes:apps/v1:Deployment"})

    spec = extract_target_spec(plan, mg)

    assert spec.arch == "riscv64-linux"


def test_spec_default_arch_when_no_node_selector():
    plan = {"nginx": {"spec": {"template": {"spec": {"containers": [{"image": "nginx:1.25"}]}}}}}
    mg = _mg_returning_types({"nginx": "kubernetes:apps/v1:Deployment"})

    spec = extract_target_spec(plan, mg)

    assert spec.arch == "x86_64-linux"


def test_spec_orchestrator_k8s_when_k8s_token_present():
    plan = {"nginx": {}}
    mg = _mg_returning_types({"nginx": "kubernetes:core/v1:Pod"})

    spec = extract_target_spec(plan, mg)

    assert spec.orchestrator == "k8s"


def test_spec_orchestrator_none_for_cape_only_program():
    plan = {"my-instance": {}}
    mg = _mg_returning_types({"my-instance": "cape:compute:Instance"})

    spec = extract_target_spec(plan, mg)

    assert spec.orchestrator is None


def test_spec_features_include_cxl_when_program_mentions_it():
    plan = {
        "node": {
            "metadata": {"labels": {"hardware.cape/cxl": "enabled"}},
            "spec": {},
        }
    }
    mg = _mg_returning_types({"node": "kubernetes:core/v1:Node"})

    spec = extract_target_spec(plan, mg)

    assert "cxl" in spec.features


def test_runtime_check_result_default_fields():
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    result = RuntimeCheckResult(passed=True, substrate="nixos-compose", spec=spec)
    assert result.passed is True
    assert result.substrate == "nixos-compose"
    assert result.spec.arch == "x86_64-linux"
    assert result.hash_1 == ""
    assert result.hash_2 == ""
    assert result.diff == []
    assert result.failures == []
    assert result.capture_duration_ms == 0


def test_runtime_check_failure_classifies_stage():
    failure = RuntimeCheckFailure(stage="provision_failed", detail="nxc build exit 1")
    assert failure.stage == "provision_failed"
    assert "nxc" in failure.detail


def test_runtime_check_result_holds_failures():
    spec = TargetSpec(arch="riscv64-linux", orchestrator="k8s")
    failures = [RuntimeCheckFailure(stage="substrate_unavailable", detail="nxc not found")]
    result = RuntimeCheckResult(passed=False, substrate="nixos-compose", spec=spec, failures=failures)
    assert len(result.failures) == 1
    assert result.failures[0].stage == "substrate_unavailable"
