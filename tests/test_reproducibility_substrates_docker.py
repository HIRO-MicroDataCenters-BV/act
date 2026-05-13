import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.docker import DockerSubstrate


@pytest.fixture
def amd64_substrate() -> DockerSubstrate:
    return DockerSubstrate(
        image="ghcr.io/example/k3s-amd64:latest",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
    )


@pytest.fixture
def riscv64_substrate() -> DockerSubstrate:
    return DockerSubstrate(
        image="ghcr.io/example/k3s-riscv64:latest",
        platform="linux/riscv64",
        spec_arch="riscv64-linux",
    )


def test_substrate_name_includes_platform(amd64_substrate, riscv64_substrate):
    # Two DockerSubstrate instances must be distinguishable in logs/artefacts.
    assert amd64_substrate.name == "docker:linux/amd64"
    assert riscv64_substrate.name == "docker:linux/riscv64"


def test_is_available_when_docker_on_path(monkeypatch, amd64_substrate):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    assert amd64_substrate.is_available() is True


def test_is_available_false_when_docker_missing(monkeypatch, amd64_substrate):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert amd64_substrate.is_available() is False


def test_matches_when_spec_arch_and_orchestrator_align(amd64_substrate):
    assert amd64_substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s")) is True


def test_does_not_match_when_arch_differs(amd64_substrate):
    assert amd64_substrate.matches(TargetSpec(arch="riscv64-linux", orchestrator="k8s")) is False


def test_does_not_match_when_orchestrator_is_none(amd64_substrate):
    assert amd64_substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator=None)) is False


def test_does_not_match_when_spec_features_outside_substrate_features():
    sub = DockerSubstrate(
        image="ghcr.io/example/cxl:latest",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"cxl"}),
    )
    # Substrate offers cxl; spec asks for fpga → no match.
    assert sub.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["fpga"])) is False


def test_matches_when_substrate_features_superset_of_spec_features():
    sub = DockerSubstrate(
        image="ghcr.io/example/cxl:latest",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"cxl"}),
    )
    # Spec asks for cxl, substrate offers cxl → match.
    assert sub.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["cxl"])) is True


def test_matches_when_spec_has_no_features_but_substrate_has(amd64_substrate):
    # An amd64 spec without features should match the plain amd64 substrate.
    assert amd64_substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=[])) is True


def _setup_provision_mocks(monkeypatch, tmp_path):
    """Common subprocess stubs for provision tests. Returns the call log."""
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "exec"]:
            # docker exec ... cat /etc/rancher/k3s/k3s.yaml
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=b"apiVersion: v1\nkind: Config\nclusters:\n- cluster:\n    server: https://127.0.0.1:6443\n",
                stderr=b"",
            )
        if cmd[:3] == ["docker", "run", "-d"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"act-container-id\n", stderr=b"")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "act.reproducibility.substrates.docker.tempfile.mkdtemp",
        lambda **kw: str(tmp_path),
    )
    monkeypatch.setattr(
        "act.reproducibility.substrates.docker.DockerSubstrate._wait_for_api",
        lambda self, container_id, port: None,
    )
    return calls


def test_provision_runs_container_with_platform_and_pinned_image(monkeypatch, tmp_path, amd64_substrate):
    calls = _setup_provision_mocks(monkeypatch, tmp_path)
    target = amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    run_calls = [c for c in calls if c[:3] == ["docker", "run", "-d"]]
    assert len(run_calls) == 1
    assert "--platform" in run_calls[0]
    assert run_calls[0][run_calls[0].index("--platform") + 1] == "linux/amd64"
    assert amd64_substrate.image in run_calls[0]
    assert target.kind == "kubeconfig"


def test_provision_returns_kubeconfig_with_rewritten_server_url(monkeypatch, tmp_path, amd64_substrate):
    _setup_provision_mocks(monkeypatch, tmp_path)
    target = amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    with open(target.endpoint) as f:
        kubeconfig = f.read()
    # Substrate's contract: rewrite the server URL to the host-forwarded port.
    assert "127.0.0.1:" in kubeconfig


def test_provision_teardown_stops_container(monkeypatch, tmp_path, amd64_substrate):
    calls = _setup_provision_mocks(monkeypatch, tmp_path)
    target = amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    pre_teardown = list(calls)
    target.teardown()
    post_teardown = calls[len(pre_teardown):]

    stop_calls = [c for c in post_teardown if c[:2] == ["docker", "stop"]]
    assert len(stop_calls) == 1


def test_provision_propagates_docker_run_failure(monkeypatch, tmp_path, amd64_substrate):
    def fake_run(cmd, *args, **kwargs):
        if cmd[:3] == ["docker", "run", "-d"]:
            raise subprocess.CalledProcessError(125, cmd, stderr=b"docker: error")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "act.reproducibility.substrates.docker.tempfile.mkdtemp",
        lambda **kw: str(tmp_path),
    )
    with pytest.raises(subprocess.CalledProcessError):
        amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))
