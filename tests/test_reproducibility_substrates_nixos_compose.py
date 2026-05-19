import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.nixos_compose import NixOSComposeSubstrate


@pytest.fixture
def substrate() -> NixOSComposeSubstrate:
    return NixOSComposeSubstrate()


def test_substrate_name(substrate):
    assert substrate.name == "nixos-compose"


def test_is_available_when_nxc_and_nix_on_path(monkeypatch, substrate):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"nxc", "nix"} else None)
    assert substrate.is_available() is True


def test_is_available_false_when_nxc_missing(monkeypatch, substrate):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nix" if name == "nix" else None)
    assert substrate.is_available() is False


def test_is_available_false_when_nix_missing(monkeypatch, substrate):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nxc" if name == "nxc" else None)
    assert substrate.is_available() is False


def test_matches_x86_64_k8s(substrate):
    assert substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s")) is True


def test_does_not_match_riscv64(substrate):
    assert substrate.matches(TargetSpec(arch="riscv64-linux", orchestrator="k8s")) is False


def test_does_not_match_when_features_include_cxl(substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["cxl"])
    assert substrate.matches(spec) is False


def test_does_not_match_when_orchestrator_is_none(substrate):
    assert substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator=None)) is False


def test_render_composition_x86_64_k8s_contains_required_directives(substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    rendered = substrate._render_composition(spec, flavour="docker")
    # Minimal contract: nix flake with k3s service exposed on the API port.
    assert "k3s" in rendered
    assert "6443" in rendered
    assert "x86_64-linux" in rendered


def test_render_composition_riscv64_raises(substrate):
    spec = TargetSpec(arch="riscv64-linux", orchestrator="k8s")
    with pytest.raises(NotImplementedError):
        substrate._render_composition(spec, flavour="docker")


def test_render_composition_unsupported_flavour_raises(substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    with pytest.raises(ValueError):
        substrate._render_composition(spec, flavour="g5k-image")


def test_provision_invokes_nxc_build_then_start_and_returns_kubeconfig(monkeypatch, tmp_path, substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    calls: list[list[str]] = []
    popen_instance = MagicMock()
    popen_instance.poll.return_value = None
    popen_instance.pid = 4242

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        # Simulate nxc creating a kubeconfig file inside the build artefacts dir.
        if cmd[:2] == ["nxc", "build"]:
            artefacts = kwargs.get("cwd") or tmp_path
            (artefacts / "kubeconfig.yaml").write_text("apiVersion: v1\nkind: Config\n")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    def fake_popen(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return popen_instance

    monkeypatch.setattr("act.reproducibility.substrates.nixos_compose.tempfile.mkdtemp", lambda **kw: str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    target = substrate.provision(spec)

    build_calls = [c for c in calls if c[:2] == ["nxc", "build"]]
    start_calls = [c for c in calls if c[:2] == ["nxc", "start"]]
    assert len(build_calls) == 1 and "-f" in build_calls[0]
    assert len(start_calls) == 1
    assert target.kind == "kubeconfig"
    assert target.endpoint.endswith("kubeconfig.yaml")


def test_provision_build_failure_propagates(monkeypatch, tmp_path, substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")

    def fake_run(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1, cmd=cmd, stderr=b"error: cannot evaluate flake"
        )

    monkeypatch.setattr("act.reproducibility.substrates.nixos_compose.tempfile.mkdtemp", lambda **kw: str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        substrate.provision(spec)


def test_teardown_kills_popen_and_runs_nxc_stop(monkeypatch, tmp_path, substrate):
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    teardown_calls: list[list[str]] = []
    popen_instance = MagicMock()
    popen_instance.poll.return_value = None

    def fake_run(cmd, *args, **kwargs):
        teardown_calls.append(list(cmd))
        if cmd[:2] == ["nxc", "build"]:
            artefacts = kwargs.get("cwd") or tmp_path
            (artefacts / "kubeconfig.yaml").write_text("apiVersion: v1\n")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    def fake_popen(cmd, *args, **kwargs):
        teardown_calls.append(list(cmd))
        return popen_instance

    monkeypatch.setattr("act.reproducibility.substrates.nixos_compose.tempfile.mkdtemp", lambda **kw: str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    target = substrate.provision(spec)

    # Reset call log so we only see teardown commands.
    teardown_calls.clear()
    target.teardown()

    assert popen_instance.terminate.called or popen_instance.kill.called
    stop_calls = [c for c in teardown_calls if c[:2] == ["nxc", "stop"]]
    assert len(stop_calls) == 1
