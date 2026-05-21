"""Generic accelerator substrate.

Three concrete accelerators (GPU, FPGA, CXL) follow the same pattern: a
regular DockerSubstrate-provisioned k3s cluster + a post-provision
`kubectl patch node --subresource=status` call that advertises a custom
Extended Resource on the node. The differences between them are entirely
configurational: which feature flag triggers them, which resource name
they advertise, and the default count.

`AcceleratorSubstrate` captures that pattern once. Concrete subclasses
set the three fields as class-level defaults:

    @dataclass
    class GpuSubstrate(AcceleratorSubstrate):
        feature_name: str = "gpu"
        resource_name: str = "nvidia.com/gpu"

The substrate's `.name` ("docker:linux/amd64+gpu"), `.matches()` (gated
on the feature flag), and `.provision()` (call super + patch node) are
all inherited.
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates._extended_resource import (
    patch_node_extended_resource,
)
from act.reproducibility.substrates.base import ProvisionedTarget, TargetSpec
from act.reproducibility.substrates.docker import DockerSubstrate


@dataclass
class AcceleratorSubstrate(DockerSubstrate):
    """Base class for accelerator substrates that declare a k8s Extended Resource.

    Subclasses set `feature_name` + `resource_name` as class-level defaults.
    """

    feature_name: str = ""
    resource_name: str = ""
    count: int = 1
    api_ready_timeout: int = 60

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"docker:{self.platform}+{self.feature_name}"

    def matches(self, spec: TargetSpec) -> bool:
        if not self.feature_name or self.feature_name not in spec.features:
            return False
        return super().matches(spec)

    def provision(self, spec: TargetSpec) -> ProvisionedTarget:
        target = super().provision(spec)
        try:
            patch_node_extended_resource(
                target.endpoint,
                self.resource_name,
                self.count,
                api_ready_timeout=self.api_ready_timeout,
            )
        except Exception:
            target.teardown()
            raise
        return target
