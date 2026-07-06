"""Generic accelerator substrate: a k3s cluster + a post-provision `kubectl patch node --subresource=status`
that advertises a custom Extended Resource.

GPU/FPGA/CXL differ only in config (feature flag, resource name, count). Subclasses set feature_name +
resource_name as class-level defaults; .name, .matches() (gated on the feature flag), and .provision() are inherited.
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
    """Accelerator substrate that declares a k8s Extended Resource; subclasses set feature_name + resource_name."""

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
