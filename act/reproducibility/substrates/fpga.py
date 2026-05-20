"""FPGA substrate: k3s + a declared `cape.eu/fpga` extended resource.

Subclasses DockerSubstrate. After provisioning the regular k3s cluster, the
substrate patches the node's status to advertise `cape.eu/fpga: N` as a
Kubernetes Extended Resource. The substrate's role is the scheduling-layer
signal — it makes FPGA-aware IaC programs deployable on any host running
k3s, with no FPGA hardware or vendor toolchain required.

The substantive verification of FPGA boot-flow correctness happens
inside the workload Pod: the user's IaC program references an HDL
simulator image (e.g. `act-fpga:iverilog`), mounts the HDL source from a
ConfigMap, and runs the simulator. The simulator's `$display` output is
captured by ACT's `probe_k8s_with_workload_logs` and included in the
hashed deployed state, so twice-and-hash verifies the boot flow runs
reproducibly across runs.

Vendor neutrality: `resource_name` defaults to `cape.eu/fpga` for
European-Sovereign-Cloud alignment but accepts any extended-resource
name (`xilinx.com/fpga`, `intel.com/fpga`, etc.). The substrate's
mechanism is identical regardless of vendor.

What this substrate does NOT do:
  - Load a bitstream onto silicon (requires vendor toolchain + hardware).
  - Parse `.bit` / `.sof` headers (that belongs to a future oracle rule).
  - Run any simulator itself — it just provides the cluster + resource.
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates._extended_resource import (
    patch_node_extended_resource,
)
from act.reproducibility.substrates.base import ProvisionedTarget, TargetSpec
from act.reproducibility.substrates.docker import DockerSubstrate


@dataclass
class FpgaSubstrate(DockerSubstrate):
    """k3s substrate with a declared `cape.eu/fpga` extended resource.

    Construct with `features=frozenset({"fpga"})` so the registry routes
    only FPGA-flagged specs here.
    """

    fpga_count: int = 1
    resource_name: str = "cape.eu/fpga"
    api_ready_timeout: int = 60

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"docker:{self.platform}+fpga"

    def matches(self, spec: TargetSpec) -> bool:
        if "fpga" not in spec.features:
            return False
        return super().matches(spec)

    def provision(self, spec: TargetSpec) -> ProvisionedTarget:
        target = super().provision(spec)
        try:
            patch_node_extended_resource(
                target.endpoint,
                self.resource_name,
                self.fpga_count,
                api_ready_timeout=self.api_ready_timeout,
            )
        except Exception:
            target.teardown()
            raise
        return target
