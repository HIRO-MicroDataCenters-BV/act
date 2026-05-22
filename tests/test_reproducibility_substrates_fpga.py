"""Unit tests for FpgaSubstrate.

Mirrors test_reproducibility_substrates_gpu.py. Subprocess is mocked so
tests run anywhere — the e2e in test_reproducibility_runtime_check_e2e.py
exercises the substrate against a real cluster.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from act.reproducibility.substrates.base import ProvisionedTarget, TargetSpec
from act.reproducibility.substrates.fpga import FpgaSubstrate


@pytest.fixture
def fpga_substrate() -> FpgaSubstrate:
    return FpgaSubstrate(
        image="rancher/k3s:v1.32.1-k3s1",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"fpga"}),
    )


def test_name_includes_platform_and_fpga_marker(fpga_substrate):
    assert fpga_substrate.name == "docker:linux/amd64+fpga"


def test_matches_fpga_flagged_spec(fpga_substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["fpga"])
    assert fpga_substrate.matches(spec) is True


def test_does_not_match_spec_without_fpga_feature(fpga_substrate):
    """Avoids stealing non-FPGA work from sibling DockerSubstrate rows."""
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=[])
    assert fpga_substrate.matches(spec) is False


def test_does_not_match_gpu_flagged_spec(fpga_substrate):
    """FPGA substrate must not catch GPU-flagged work."""
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["gpu"])
    assert fpga_substrate.matches(spec) is False


def test_is_available_inherits_from_docker_substrate(monkeypatch, fpga_substrate):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    assert fpga_substrate.is_available() is True


def test_provision_patches_node_status_with_fpga_extended_resource(fpga_substrate):
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
        mock_run.side_effect = [
            MagicMock(stdout=b"k3s-node-0", returncode=0),
            MagicMock(stdout=b"", returncode=0),
        ]
        target = fpga_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["fpga"]))

    mock_parent.assert_called_once()
    assert target is parent_target

    # Patch call should target both capacity and allocatable on the cape.eu/fpga key.
    patch_args = mock_run.call_args_list[1].args[0]
    assert "patch" in patch_args and "node" in patch_args
    assert "k3s-node-0" in patch_args
    patch_json = json.loads(patch_args[-1])
    assert patch_json == [
        {"op": "add", "path": "/status/capacity/cape.eu~1fpga", "value": "1"},
        {"op": "add", "path": "/status/allocatable/cape.eu~1fpga", "value": "1"},
    ]


def test_provision_calls_teardown_when_patch_fails(fpga_substrate):
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
            fpga_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["fpga"]))

    teardown.assert_called_once()


def test_custom_resource_name_and_count_honoured():
    sub = FpgaSubstrate(
        image="rancher/k3s:v1.32.1-k3s1",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"fpga"}),
        resource_name="xilinx.com/fpga",
        count=2,
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
            MagicMock(stdout=b"node-y", returncode=0),
            MagicMock(stdout=b"", returncode=0),
        ]
        sub.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["fpga"]))

    patch_json = json.loads(mock_run.call_args_list[1].args[0][-1])
    assert patch_json[0]["path"] == "/status/capacity/xilinx.com~1fpga"
    assert patch_json[0]["value"] == "2"
    assert patch_json[1]["path"] == "/status/allocatable/xilinx.com~1fpga"
    assert patch_json[1]["value"] == "2"
