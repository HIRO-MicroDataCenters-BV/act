"""CXL substrate: k3s + a declared `cape.eu/cxl` extended resource.

Concrete `AcceleratorSubstrate` specialisation. See
`substrates/accelerator.py` for the shared mechanism. The substrate's
role is the scheduling-layer signal — it makes CXL-aware IaC programs
deployable on any host running k3s, without requiring real CXL hardware.

The substantive verification of CXL device behaviour happens inside the
workload Pod: the user's IaC program references the `act-cxl:qemu` image,
whose entrypoint runs `qemu-system-x86_64` with a `cxl-type3` device on
a Linux 6.5+ guest. The guest boots, loads the CXL kernel modules, runs
`cxl list -v`, and prints the device topology. ACT captures this output
via `probe_k8s_with_workload_logs` and includes it in the hashed deployed
state.

Vendor neutrality: `resource_name` defaults to `cape.eu/cxl` for
European-Sovereign-Cloud alignment but accepts any extended-resource
name.
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates.accelerator import AcceleratorSubstrate


@dataclass
class CxlSubstrate(AcceleratorSubstrate):
    """k3s substrate with a declared `cape.eu/cxl` extended resource."""

    feature_name: str = "cxl"
    resource_name: str = "cape.eu/cxl"
