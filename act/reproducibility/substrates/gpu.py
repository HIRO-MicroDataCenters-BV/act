"""GPU substrate: k3s + a declared `nvidia.com/gpu` extended resource.

Concrete `AcceleratorSubstrate` specialisation. See
`substrates/accelerator.py` for the shared mechanism. The substrate
validates the IaC layer of GPU-aware Pulumi programs (resource
declarations, scheduling onto GPU-flagged nodes, reproducibility of the
deployed state). Real CUDA execution inside scheduled pods is **not**
verified — the Extended Resource patch is a scheduling-layer signal,
not a hardware exposure. Substantive runtime GPU verification requires
real GPU hardware on the host.

Vendor neutrality: the `resource_name` field defaults to `nvidia.com/gpu`
(the universally-recognized k8s GPU resource name) but accepts any
extended-resource string — `amd.com/gpu`, `intel.com/gpu`, or a
CAPE-specific identifier like `cape.eu/accelerator`.
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates.accelerator import AcceleratorSubstrate


@dataclass
class GpuSubstrate(AcceleratorSubstrate):
    """k3s substrate with a declared `nvidia.com/gpu` extended resource.

    Construct with `features=frozenset({"gpu"})` so the registry routes
    only GPU-flagged specs here, not non-GPU work.
    """

    feature_name: str = "gpu"
    resource_name: str = "nvidia.com/gpu"
