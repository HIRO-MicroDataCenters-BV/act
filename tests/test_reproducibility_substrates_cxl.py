"""Unit tests for CxlSubstrate.

Mirrors test_reproducibility_substrates_fpga.py. Subprocess is mocked so
tests run anywhere — the e2e exercises the substrate against a real
cluster + the act-cxl:qemu workload image.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from act.reproducibility.substrates.base import ProvisionedTarget, TargetSpec
from act.reproducibility.substrates.cxl import CxlSubstrate


@pytest.fixture
def cxl_substrate() -> CxlSubstrate:
    return CxlSubstrate(
        image="rancher/k3s:v1.32.1-k3s1",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"cxl"}),
    )


def test_name_includes_platform_and_cxl_marker(cxl_substrate):
    assert cxl_substrate.name == "docker:linux/amd64+cxl"


def test_matches_cxl_flagged_spec(cxl_substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["cxl"])
    assert cxl_substrate.matches(spec) is True


def test_does_not_match_spec_without_cxl_feature(cxl_substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=[])
    assert cxl_substrate.matches(spec) is False


def test_does_not_match_gpu_or_fpga_flagged_spec(cxl_substrate):
    """CXL substrate must not catch GPU or FPGA work."""
    for feature in ("gpu", "fpga"):
        spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=[feature])
        assert cxl_substrate.matches(spec) is False, f"matched {feature!r}"


def test_is_available_inherits_from_docker_substrate(monkeypatch, cxl_substrate):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    assert cxl_substrate.is_available() is True


def test_provision_patches_node_status_with_cxl_extended_resource(cxl_substrate):
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
        target = cxl_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["cxl"]))

    mock_parent.assert_called_once()
    assert target is parent_target

    patch_args = mock_run.call_args_list[1].args[0]
    assert "patch" in patch_args and "node" in patch_args
    assert "k3s-node-0" in patch_args
    patch_json = json.loads(patch_args[-1])
    assert patch_json == [
        {"op": "add", "path": "/status/capacity/cape.eu~1cxl", "value": "1"},
        {"op": "add", "path": "/status/allocatable/cape.eu~1cxl", "value": "1"},
    ]


def test_provision_calls_teardown_when_patch_fails(cxl_substrate):
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
            cxl_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["cxl"]))

    teardown.assert_called_once()
