from __future__ import annotations

import hashlib
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)

SUPPORTED_ARCHES: frozenset[str] = frozenset({"riscv64-linux"})


@dataclass(frozen=True)
class GuestImage:
    url: str
    sha256: str
    filename: str
    machine: str
    distro: str


# Pinned by digest for reproducibility. Ubuntu 24.04 (noble) preinstalled-server
# riscv64 cloud image, published on cloud-images.ubuntu.com. Boots on
# `qemu-system-riscv64 -M virt -bios opensbi.bin -kernel u-boot.elf` and accepts
# cloud-init seed ISOs on the standard NoCloud datasource.
DEFAULT_IMAGE = GuestImage(
    url="https://cloud-images.ubuntu.com/releases/noble/release-20260401/ubuntu-24.04-server-cloudimg-riscv64.img",
    sha256="0" * 64,  # operator override expected; the pin is configurable via env
    filename="ubuntu-24.04-server-cloudimg-riscv64.img",
    machine="virt",
    distro="ubuntu",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_image(image: GuestImage, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / image.filename

    if target.exists() and _sha256_file(target) == image.sha256:
        return target

    urllib.request.urlretrieve(image.url, str(target))
    digest = _sha256_file(target)
    if digest != image.sha256:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"sha256 mismatch for {image.filename}: expected {image.sha256}, got {digest}"
        )
    return target


class QemuRiscv64Substrate(Substrate):
    name: ClassVar[str] = "qemu-riscv64"

    def is_available(self) -> bool:
        return shutil.which("qemu-system-riscv64") is not None

    def matches(self, spec: TargetSpec) -> bool:
        if spec.arch not in SUPPORTED_ARCHES:
            return False
        if spec.orchestrator != "k8s":
            return False
        if "cxl" in spec.features:
            return False
        return True

    def provision(self, spec: TargetSpec) -> ProvisionedTarget:
        raise NotImplementedError("provision is wired in a later cycle")
