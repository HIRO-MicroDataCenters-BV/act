"""Minimal k8s fixture used by the runtime-check e2e.

A ConfigMap is an API-only resource: the k8s API server stores the object,
no image is pulled, no pod is scheduled. That keeps the e2e fast and
independent of image-registry reachability from inside the substrate
container (a real concern under QEMU emulation, where image pulls inside
a privileged k3s container time out before the test does).

The substantive reproducibility claim — "executing the same program twice
on the target platform and comparing the output hashes" — doesn't require
a runtime workload; it requires a deterministic deployed state. A
ConfigMap exercises the substrate-driven pulumi up + kubectl probe path
end-to-end without the pod scheduling tax.
"""

import pulumi
from pulumi_kubernetes.core.v1 import ConfigMap

cm = ConfigMap(
    "act-runtime-probe",
    metadata={"name": "act-runtime-probe", "namespace": "default"},
    data={"key": "value"},
)

pulumi.export("name", cm.metadata["name"])
