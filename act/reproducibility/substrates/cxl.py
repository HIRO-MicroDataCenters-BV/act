"""CXL substrate: k3s + a declared `cape.eu/cxl` extended resource.

Scheduling-layer signal only: makes CXL-aware IaC deployable on any k3s host, no CXL hardware needed.
Real device verification happens in the workload Pod, which runs `act-cxl:qemu` (qemu-system-x86_64 with a
`cxl-type3` device on a Linux 6.5+ guest that runs `cxl list -v`); its output is captured by
`probe_k8s_with_workload_logs` and hashed into the deployed state. `resource_name` defaults to `cape.eu/cxl`.
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates.accelerator import AcceleratorSubstrate


@dataclass
class CxlSubstrate(AcceleratorSubstrate):
    """Construct with `features=frozenset({"cxl"})` so the registry routes only CXL-flagged specs here."""

    feature_name: str = "cxl"
    resource_name: str = "cape.eu/cxl"
