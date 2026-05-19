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
