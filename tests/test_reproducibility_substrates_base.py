import pytest

from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)


def test_target_spec_dataclass_shape():
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    assert spec.arch == "x86_64-linux"
    assert spec.orchestrator == "k8s"
    assert spec.features == []


def test_target_spec_features_default_to_empty_list():
    a = TargetSpec(arch="x86_64-linux", orchestrator=None)
    b = TargetSpec(arch="riscv64-linux", orchestrator="k8s")
    a.features.append("cxl")
    assert b.features == []


def test_provisioned_target_endpoint_and_teardown():
    teardowns: list = []
    target = ProvisionedTarget(
        endpoint="/tmp/kube.config",
        kind="kubeconfig",
        teardown=lambda: teardowns.append("done"),
    )
    assert target.endpoint == "/tmp/kube.config"
    assert target.kind == "kubeconfig"
    target.teardown()
    assert teardowns == ["done"]


def test_substrate_abc_requires_methods():
    with pytest.raises(TypeError):
        Substrate()  # type: ignore[abstract]


def test_substrate_subclass_must_implement_all_abstract_methods():
    class HalfBaked(Substrate):
        name = "half-baked"

        def matches(self, spec):
            return True

    with pytest.raises(TypeError):
        HalfBaked()  # type: ignore[abstract]


def test_substrate_complete_subclass_instantiates():
    class Complete(Substrate):
        name = "complete"

        def matches(self, spec):
            return True

        def provision(self, spec):
            return ProvisionedTarget(endpoint="x", kind="ssh", teardown=lambda: None)

        def is_available(self):
            return True

    inst = Complete()
    assert inst.name == "complete"
    assert inst.matches(TargetSpec(arch="x86_64-linux", orchestrator=None)) is True
    assert inst.is_available() is True
