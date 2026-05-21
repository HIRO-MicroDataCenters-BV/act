"""FPGA substrate: k3s + a declared `cape.eu/fpga` extended resource.

Concrete `AcceleratorSubstrate` specialisation. See
`substrates/accelerator.py` for the shared mechanism. The substrate's
role is the scheduling-layer signal — it makes FPGA-aware IaC programs
deployable on any host running k3s, with no FPGA hardware or vendor
toolchain required.

The substantive verification of FPGA boot-flow correctness happens
inside the workload Pod: the user's IaC program references an HDL
simulator image (e.g. `act-fpga:iverilog`), mounts the HDL source from a
ConfigMap, and runs the simulator. The simulator's `$display` output is
captured by ACT's `probe_k8s_with_workload_logs` and included in the
hashed deployed state.

Vendor neutrality: `resource_name` defaults to `cape.eu/fpga` for
European-Sovereign-Cloud alignment but accepts any extended-resource
name (`xilinx.com/fpga`, `intel.com/fpga`, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates.accelerator import AcceleratorSubstrate


@dataclass
class FpgaSubstrate(AcceleratorSubstrate):
    """k3s substrate with a declared `cape.eu/fpga` extended resource."""

    feature_name: str = "fpga"
    resource_name: str = "cape.eu/fpga"
