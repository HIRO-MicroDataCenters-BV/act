from __future__ import annotations

import shutil
from typing import ClassVar

from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)

SUPPORTED_ARCHES: frozenset[str] = frozenset({"riscv64-linux"})


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
