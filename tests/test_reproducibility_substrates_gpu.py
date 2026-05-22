"""Unit tests for GpuSubstrate.

Mocks subprocess so the tests run anywhere — no docker, kubectl, or k3s
required. The e2e in test_reproducibility_runtime_check_e2e.py exercises
the substrate against a real cluster.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from act.reproducibility.substrates.base import ProvisionedTarget, TargetSpec
from act.reproducibility.substrates.gpu import GpuSubstrate


@pytest.fixture
def gpu_substrate() -> GpuSubstrate:
    return GpuSubstrate(
        image="rancher/k3s:v1.32.1-k3s1",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"gpu"}),
    )


def test_name_includes_platform_and_gpu_marker(gpu_substrate):
    assert gpu_substrate.name == "docker:linux/amd64+gpu"


def test_matches_gpu_flagged_spec(gpu_substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["gpu"])
    assert gpu_substrate.matches(spec) is True


def test_does_not_match_spec_without_gpu_feature(gpu_substrate):
    """Avoids stealing non-GPU work from sibling DockerSubstrate rows."""
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=[])
    assert gpu_substrate.matches(spec) is False


def test_does_not_match_when_arch_differs(gpu_substrate):
    spec = TargetSpec(arch="aarch64-linux", orchestrator="k8s", features=["gpu"])
    assert gpu_substrate.matches(spec) is False


def test_does_not_match_when_orchestrator_differs(gpu_substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator=None, features=["gpu"])
    assert gpu_substrate.matches(spec) is False


def test_is_available_inherits_from_docker_substrate(monkeypatch, gpu_substrate):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    assert gpu_substrate.is_available() is True


def test_is_available_false_when_docker_missing(monkeypatch, gpu_substrate):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert gpu_substrate.is_available() is False


def test_provision_patches_node_status_with_extended_resource(gpu_substrate):
    """provision() must call DockerSubstrate.provision then patch the node status."""
    parent_target = ProvisionedTarget(
        endpoint="/tmp/fake/kubeconfig",
        kind="kubeconfig",
        teardown=MagicMock(),
    )

    with (
        patch(
            "act.reproducibility.substrates.accelerator.DockerSubstrate.provision",
            return_value=parent_target,
        ) as mock_parent,
        patch("act.reproducibility.substrates._extended_resource.subprocess.run") as mock_run,
    ):
        # First subprocess call returns the node name; second is the patch.
        mock_run.side_effect = [
            MagicMock(stdout=b"k3s-node-0", returncode=0),
            MagicMock(stdout=b"", returncode=0),
        ]
        target = gpu_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["gpu"]))

    mock_parent.assert_called_once()
    assert target is parent_target

    # First call: get node name.
    first_args = mock_run.call_args_list[0].args[0]
    assert "get" in first_args and "nodes" in first_args
    assert "--kubeconfig" in first_args
    assert "/tmp/fake/kubeconfig" in first_args

    # Second call: patch node status with both capacity and allocatable.
    second_args = mock_run.call_args_list[1].args[0]
    assert "patch" in second_args and "node" in second_args
    assert "k3s-node-0" in second_args
    assert "--subresource=status" in second_args
    # The JSON patch is the last positional after -p.
    patch_json = json.loads(second_args[-1])
    assert patch_json == [
        {"op": "add", "path": "/status/capacity/nvidia.com~1gpu", "value": "1"},
        {"op": "add", "path": "/status/allocatable/nvidia.com~1gpu", "value": "1"},
    ]


def test_provision_calls_teardown_when_patch_fails(gpu_substrate):
    """If the Extended Resource patch errors, the parent target must be torn down."""
    teardown = MagicMock()
    parent_target = ProvisionedTarget(
        endpoint="/tmp/fake/kubeconfig",
        kind="kubeconfig",
        teardown=teardown,
    )

    with (
        patch(
            "act.reproducibility.substrates.accelerator.DockerSubstrate.provision",
            return_value=parent_target,
        ),
        patch(
            "act.reproducibility.substrates._extended_resource.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "kubectl"),
        ),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            gpu_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["gpu"]))

    teardown.assert_called_once()


def test_custom_resource_name_is_honoured():
    sub = GpuSubstrate(
        image="rancher/k3s:v1.32.1-k3s1",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"gpu"}),
        resource_name="amd.com/gpu",
        count=4,
    )
    parent_target = ProvisionedTarget(
        endpoint="/tmp/k",
        kind="kubeconfig",
        teardown=MagicMock(),
    )
    with (
        patch(
            "act.reproducibility.substrates.accelerator.DockerSubstrate.provision",
            return_value=parent_target,
        ),
        patch("act.reproducibility.substrates._extended_resource.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = [
            MagicMock(stdout=b"node-x", returncode=0),
            MagicMock(stdout=b"", returncode=0),
        ]
        sub.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["gpu"]))

    patch_json = json.loads(mock_run.call_args_list[1].args[0][-1])
    assert patch_json[0]["path"] == "/status/capacity/amd.com~1gpu"
    assert patch_json[0]["value"] == "4"
    assert patch_json[1]["path"] == "/status/allocatable/amd.com~1gpu"
    assert patch_json[1]["value"] == "4"
