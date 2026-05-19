"""GPU substrate: k3s + a declared `nvidia.com/gpu` extended resource.

Subclasses DockerSubstrate. After provisioning the regular k3s cluster, the
substrate patches the node's status to advertise `nvidia.com/gpu: N` as a
Kubernetes Extended Resource. Extended resources are persistent — kubelet
does not overwrite them — so they remain schedulable for the cluster's
lifetime.

This validates the IaC layer of GPU-aware Pulumi programs:

  - resource declarations (e.g. `resources.limits["nvidia.com/gpu"] = 1`),
  - scheduling onto GPU-flagged nodes,
  - reproducibility of the deployed state across runs.

Real CUDA execution inside scheduled pods is **not** verified — the
extended-resource patch is a scheduling-layer signal, not a hardware
exposure. Substantive runtime GPU verification requires real GPU hardware
on the host; in that case, point ACT at the host's existing GPU-equipped
kubeconfig directly. No general-purpose GPU emulator exists for k8s.

Vendor neutrality: the `resource_name` field defaults to `nvidia.com/gpu`
(the universally-recognized k8s GPU resource name), but accepts any
extended-resource name — `amd.com/gpu`, `intel.com/gpu`, or a
CAPE-specific identifier like `cape.eu/accelerator`. The substrate's
mechanism is identical regardless of vendor.
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates._extended_resource import (
    patch_node_extended_resource,
)
from act.reproducibility.substrates.base import ProvisionedTarget, TargetSpec
from act.reproducibility.substrates.docker import DockerSubstrate


@dataclass
class GpuSubstrate(DockerSubstrate):
    """k3s substrate with a declared `nvidia.com/gpu` extended resource.

    Construct with `features=frozenset({"gpu"})` so the registry routes
    only GPU-flagged specs here, not non-GPU work.
    """

    gpu_count: int = 1
    resource_name: str = "nvidia.com/gpu"
    api_ready_timeout: int = 60

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"docker:{self.platform}+gpu"

    def matches(self, spec: TargetSpec) -> bool:
        if "gpu" not in spec.features:
            return False
        return super().matches(spec)

    def provision(self, spec: TargetSpec) -> ProvisionedTarget:
        target = super().provision(spec)
        try:
            patch_node_extended_resource(
                target.endpoint,
                self.resource_name,
                self.gpu_count,
                api_ready_timeout=self.api_ready_timeout,
            )
        except Exception:
            target.teardown()
            raise
        return target
