from unittest.mock import MagicMock

from act.reproducibility.runtime_check import extract_target_spec


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
