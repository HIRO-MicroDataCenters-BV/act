"""CXL boot-flow fixture: Job that boots a Linux 6.5+ guest under QEMU
with a CXL Type 3 memory device and dumps `cxl list -v` output.

Deploys against any cluster reporting `cape.eu/cxl` as a schedulable
resource (CxlSubstrate sets this up via Extended Resource patch). The
Job's container runs the `act-cxl:qemu` image whose entrypoint runs
qemu-system-x86_64 with `-device cxl-type3,volatile-memdev=...`. The
guest boots, loads the CXL kernel modules, prints the topology JSON
via `cxl list -v`, halts. ACT captures the deterministic JSON via
probe_k8s_with_workload_logs and includes it in the hashed state.

Docker Desktop's k3s sandbox doesn't ship the host seccomp profile —
the Pod sets `seccompProfile: Unconfined` to match the FPGA fixture.
"""

import os

import pulumi
from pulumi_kubernetes.batch.v1 import Job

IMAGE = os.environ.get("ACT_CXL_QEMU_IMAGE", "act-cxl:qemu")

job = Job(
    "cxl-boot-flow",
    metadata={"name": "cxl-boot-flow", "namespace": "default"},
    spec={
        "backoffLimit": 0,
        "template": {
            "metadata": {"labels": {"app": "cxl-boot-flow"}},
            "spec": {
                "restartPolicy": "Never",
                "securityContext": {"seccompProfile": {"type": "Unconfined"}},
                "containers": [{
                    "name": "qemu-cxl",
                    "image": IMAGE,
                    "imagePullPolicy": "IfNotPresent",
                    "resources": {
                        "limits": {"cape.eu/cxl": "1"},
                        "requests": {"cape.eu/cxl": "1"},
                    },
                }],
            },
        },
    },
)

pulumi.export("job_name", job.metadata["name"])
