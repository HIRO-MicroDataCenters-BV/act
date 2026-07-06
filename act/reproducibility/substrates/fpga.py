"""FPGA substrate: k3s + a declared `cape.eu/fpga` extended resource.

Scheduling-layer signal only: makes FPGA-aware IaC deployable on any k3s host, no FPGA hardware needed.
Real boot-flow verification happens in the workload Pod, which runs an HDL simulator image (e.g.
`act-fpga:iverilog`); its `$display` output is captured by `probe_k8s_with_workload_logs` and hashed into
the deployed state. `resource_name` defaults to `cape.eu/fpga` but accepts any vendor string.
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates.accelerator import AcceleratorSubstrate


@dataclass
class FpgaSubstrate(AcceleratorSubstrate):
    """k3s substrate with a declared `cape.eu/fpga` extended resource."""

    feature_name: str = "fpga"
    resource_name: str = "cape.eu/fpga"
