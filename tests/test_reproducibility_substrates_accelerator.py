"""Unit tests shared by the accelerator substrates (Gpu/Fpga/Cxl).

They all subclass AcceleratorSubstrate and differ only in feature_name /
resource_name, so the behaviour is exercised once here, parametrized over the
three. Subprocess is mocked so the tests run anywhere; the e2e in
test_reproducibility_runtime_check_e2e.py exercises them against a real cluster.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from act.reproducibility.substrates.base import ProvisionedTarget, TargetSpec
from act.reproducibility.substrates.cxl import CxlSubstrate
from act.reproducibility.substrates.fpga import FpgaSubstrate
from act.reproducibility.substrates.gpu import GpuSubstrate

# (SubstrateClass, feature_name, default resource_name)
ACCELERATORS = [
    pytest.param(GpuSubstrate, "gpu", "nvidia.com/gpu", id="gpu"),
    pytest.param(FpgaSubstrate, "fpga", "cape.eu/fpga", id="fpga"),
    pytest.param(CxlSubstrate, "cxl", "cape.eu/cxl", id="cxl"),
]
FEATURES = ("gpu", "fpga", "cxl")


def _make(cls, feature, **kw):
    return cls(
        image="rancher/k3s:v1.32.1-k3s1",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({feature}),
        **kw,
    )


def _spec(features, arch="x86_64-linux", orchestrator="k8s"):
    return TargetSpec(arch=arch, orchestrator=orchestrator, features=list(features))


@contextmanager
def _mock_provision(run_side_effect=None):
    """Patch DockerSubstrate.provision + the extended-resource subprocess.run."""
    parent = ProvisionedTarget(endpoint="/tmp/fake/kubeconfig", kind="kubeconfig", teardown=MagicMock())
    with (
        patch(
            "act.reproducibility.substrates.accelerator.DockerSubstrate.provision", return_value=parent
        ) as mock_parent,
        patch("act.reproducibility.substrates._extended_resource.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = run_side_effect or [
            MagicMock(stdout=b"k3s-node-0", returncode=0),  # get node name
            MagicMock(stdout=b"", returncode=0),  # patch status
        ]
        yield parent, mock_parent, mock_run


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_name_includes_platform_and_feature_marker(cls, feature, resource_name):
    assert _make(cls, feature).name == f"docker:linux/amd64+{feature}"


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_matches_own_feature_flagged_spec(cls, feature, resource_name):
    assert _make(cls, feature).matches(_spec([feature])) is True


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_does_not_match_spec_without_features(cls, feature, resource_name):
    """Avoids stealing non-accelerator work from sibling DockerSubstrate rows."""
    assert _make(cls, feature).matches(_spec([])) is False


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_does_not_match_when_arch_differs(cls, feature, resource_name):
    assert _make(cls, feature).matches(_spec([feature], arch="aarch64-linux")) is False


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_does_not_match_when_orchestrator_is_none(cls, feature, resource_name):
    assert _make(cls, feature).matches(_spec([feature], orchestrator=None)) is False


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_does_not_match_other_accelerator_feature(cls, feature, resource_name):
    """Each accelerator must not catch work flagged for a different accelerator."""
    sub = _make(cls, feature)
    for other in (f for f in FEATURES if f != feature):
        assert sub.matches(_spec([other])) is False, f"{feature} matched {other!r}"


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_is_available_true_when_all_tools_present(monkeypatch, cls, feature, resource_name):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert _make(cls, feature).is_available() is True


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_is_available_false_when_docker_missing(monkeypatch, cls, feature, resource_name):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _make(cls, feature).is_available() is False


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_provision_patches_node_status_with_extended_resource(cls, feature, resource_name):
    """provision() calls DockerSubstrate.provision then patches the node status."""
    sub = _make(cls, feature)
    with _mock_provision() as (parent, mock_parent, mock_run):
        target = sub.provision(_spec([feature]))

    mock_parent.assert_called_once()
    assert target is parent

    first_args = mock_run.call_args_list[0].args[0]
    assert "get" in first_args and "nodes" in first_args
    assert "--kubeconfig" in first_args and "/tmp/fake/kubeconfig" in first_args

    patch_args = mock_run.call_args_list[1].args[0]
    assert "patch" in patch_args and "node" in patch_args
    assert "k3s-node-0" in patch_args and "--subresource=status" in patch_args
    key = resource_name.replace("/", "~1")
    assert json.loads(patch_args[-1]) == [
        {"op": "add", "path": f"/status/capacity/{key}", "value": "1"},
        {"op": "add", "path": f"/status/allocatable/{key}", "value": "1"},
    ]


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_provision_calls_teardown_when_patch_fails(cls, feature, resource_name):
    sub = _make(cls, feature)
    with _mock_provision(run_side_effect=subprocess.CalledProcessError(1, "kubectl")) as (parent, _mp, _mr):
        with pytest.raises(subprocess.CalledProcessError):
            sub.provision(_spec([feature]))
    parent.teardown.assert_called_once()


@pytest.mark.parametrize("cls, feature, resource_name", ACCELERATORS)
def test_custom_resource_name_and_count_honoured(cls, feature, resource_name):
    sub = _make(cls, feature, resource_name=f"vendor.com/{feature}", count=4)
    with _mock_provision() as (_parent, _mp, mock_run):
        sub.provision(_spec([feature]))
    key = f"vendor.com~1{feature}"
    patch_json = json.loads(mock_run.call_args_list[1].args[0][-1])
    assert patch_json[0] == {"op": "add", "path": f"/status/capacity/{key}", "value": "4"}
    assert patch_json[1] == {"op": "add", "path": f"/status/allocatable/{key}", "value": "4"}
