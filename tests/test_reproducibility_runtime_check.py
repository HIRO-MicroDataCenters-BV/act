from unittest.mock import MagicMock, patch

import pytest

from act.reproducibility.runtime_check import (
    RuntimeCheckFailure,
    RuntimeCheckResult,
    extract_target_spec,
    run_pulumi_against,
)
from act.reproducibility.substrates.base import ProvisionedTarget, TargetSpec


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


def _provisioned() -> ProvisionedTarget:
    return ProvisionedTarget(
        endpoint="/tmp/kube.config",
        kind="kubeconfig",
        teardown=lambda: None,
    )


def test_run_pulumi_against_invokes_up_and_destroy(tmp_path):
    stack = MagicMock()
    stack.up.return_value = MagicMock(outputs={"endpoint": MagicMock(value="ok")})
    stack.destroy.return_value = MagicMock()

    with patch(
        "act.reproducibility.runtime_check.automation.create_or_select_stack",
        return_value=stack,
    ):
        outcome = run_pulumi_against(
            target=_provisioned(),
            program_path="some.py",
            backend_dir=str(tmp_path),
        )

    assert outcome.failure is None
    stack.up.assert_called_once()
    stack.destroy.assert_called_once()


def test_run_pulumi_against_destroys_on_up_failure(tmp_path):
    stack = MagicMock()
    stack.up.side_effect = RuntimeError("provider rejected manifest")
    stack.destroy.return_value = MagicMock()

    with patch(
        "act.reproducibility.runtime_check.automation.create_or_select_stack",
        return_value=stack,
    ):
        outcome = run_pulumi_against(
            target=_provisioned(),
            program_path="some.py",
            backend_dir=str(tmp_path),
        )

    assert outcome.failure is not None
    assert outcome.failure.stage == "pulumi_up_failed"
    assert "provider rejected manifest" in outcome.failure.detail
    stack.destroy.assert_called_once()


def test_run_pulumi_against_sets_kubeconfig_config(tmp_path):
    stack = MagicMock()
    stack.up.return_value = MagicMock(outputs={})
    stack.destroy.return_value = MagicMock()

    with patch(
        "act.reproducibility.runtime_check.automation.create_or_select_stack",
        return_value=stack,
    ):
        run_pulumi_against(
            target=_provisioned(),
            program_path="some.py",
            backend_dir=str(tmp_path),
        )

    set_config_calls = stack.set_config.call_args_list
    keys = [call.args[0] for call in set_config_calls]
    assert "kubernetes:kubeconfig" in keys
