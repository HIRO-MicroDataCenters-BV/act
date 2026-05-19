import shutil

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
