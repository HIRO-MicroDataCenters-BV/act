from __future__ import annotations

import shutil
from typing import ClassVar

from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)

SUPPORTED_ARCHES: frozenset[str] = frozenset({"x86_64-linux"})


class NixOSComposeSubstrate(Substrate):
    name: ClassVar[str] = "nixos-compose"

    def is_available(self) -> bool:
        return shutil.which("nxc") is not None and shutil.which("nix") is not None

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
