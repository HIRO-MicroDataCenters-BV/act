import shutil

import pytest

from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.qemu_riscv64 import QemuRiscv64Substrate


@pytest.fixture
def substrate() -> QemuRiscv64Substrate:
    return QemuRiscv64Substrate()


def test_substrate_name(substrate):
    assert substrate.name == "qemu-riscv64"


def test_is_available_when_qemu_system_riscv64_on_path(monkeypatch, substrate):
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/bin/qemu-system-riscv64" if name == "qemu-system-riscv64" else None,
    )
    assert substrate.is_available() is True


def test_is_available_false_when_qemu_missing(monkeypatch, substrate):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert substrate.is_available() is False


def test_matches_riscv64_k8s(substrate):
    assert substrate.matches(TargetSpec(arch="riscv64-linux", orchestrator="k8s")) is True


def test_does_not_match_x86_64(substrate):
    assert substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s")) is False


def test_does_not_match_aarch64(substrate):
    assert substrate.matches(TargetSpec(arch="aarch64-linux", orchestrator="k8s")) is False


def test_does_not_match_when_orchestrator_is_none(substrate):
    assert substrate.matches(TargetSpec(arch="riscv64-linux", orchestrator=None)) is False


def test_does_not_match_when_features_include_cxl(substrate):
    spec = TargetSpec(arch="riscv64-linux", orchestrator="k8s", features=["cxl"])
    assert substrate.matches(spec) is False
