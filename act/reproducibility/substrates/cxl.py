"""CXL substrate: k3s + a declared `cape.eu/cxl` extended resource.

Subclasses DockerSubstrate. After provisioning the regular k3s cluster, the
substrate patches the node's status to advertise `cape.eu/cxl: N` as a
Kubernetes Extended Resource. The substrate's role is the scheduling-layer
signal — it makes CXL-aware IaC programs deployable on any host running
k3s, without requiring real CXL hardware.

The substantive verification of CXL device behaviour happens inside the
workload Pod: the user's IaC program references the `act-cxl:qemu` image,
whose entrypoint runs `qemu-system-x86_64` with a `cxl-type3` device on a
Linux 6.5+ guest. The guest boots, loads the CXL kernel modules, runs
`cxl list -v`, and prints the device topology. ACT captures this output
via `probe_k8s_with_workload_logs` and includes it in the hashed deployed
state, so twice-and-hash verifies the CXL bring-up runs reproducibly.

Vendor neutrality: `resource_name` defaults to `cape.eu/cxl` for
European-Sovereign-Cloud alignment but accepts any extended-resource
name — the substrate mechanism is identical regardless.

What this substrate does NOT do:
  - Provide real CXL hardware (no general-purpose CXL emulator exists
    outside QEMU; physical CXL devices require vendor toolchains).
  - Run QEMU itself — that lives in the workload Pod's image.
"""

from __future__ import annotations

from dataclasses import dataclass

from act.reproducibility.substrates._extended_resource import (
    patch_node_extended_resource,
)
from act.reproducibility.substrates.base import ProvisionedTarget, TargetSpec
from act.reproducibility.substrates.docker import DockerSubstrate


@dataclass
class CxlSubstrate(DockerSubstrate):
    """k3s substrate with a declared `cape.eu/cxl` extended resource.

    Construct with `features=frozenset({"cxl"})` so the registry routes
    only CXL-flagged specs here.
    """

    cxl_count: int = 1
    resource_name: str = "cape.eu/cxl"
    api_ready_timeout: int = 60

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"docker:{self.platform}+cxl"

    def matches(self, spec: TargetSpec) -> bool:
        if "cxl" not in spec.features:
            return False
        return super().matches(spec)

    def provision(self, spec: TargetSpec) -> ProvisionedTarget:
        target = super().provision(spec)
        try:
            patch_node_extended_resource(
                target.endpoint,
                self.resource_name,
                self.cxl_count,
                api_ready_timeout=self.api_ready_timeout,
            )
        except Exception:
            target.teardown()
            raise
        return target
