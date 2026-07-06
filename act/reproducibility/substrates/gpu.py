"""GPU substrate: k3s + a declared `nvidia.com/gpu` extended resource.

The Extended Resource is a scheduling-layer signal, not a hardware exposure: this validates GPU-aware IaC
(declarations, scheduling onto GPU-flagged nodes, reproducibility), not real CUDA execution (which needs GPU
hardware). `resource_name` defaults to `nvidia.com/gpu` but accepts any vendor string (`amd.com/gpu`, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates.accelerator import AcceleratorSubstrate


@dataclass
class GpuSubstrate(AcceleratorSubstrate):
    """k3s substrate declaring `nvidia.com/gpu`.

    Construct with `features=frozenset({"gpu"})` so the registry routes only GPU-flagged specs here.
    """

    feature_name: str = "gpu"
    resource_name: str = "nvidia.com/gpu"
