import pulumi
from pulumi_kubernetes.apps.v1 import Deployment

# riscv64-targeted workload for the reproducibility runtime demo: busybox has a riscv64
# variant and stays running under `sleep`, reaching steady state deterministically.
deployment = Deployment(
    "sleeper",
    spec={
        "replicas": 1,
        "selector": {"matchLabels": {"app": "sleeper"}},
        "template": {
            "metadata": {"labels": {"app": "sleeper"}},
            "spec": {
                "nodeSelector": {"kubernetes.io/arch": "riscv64"},
                "containers": [{"name": "sleeper", "image": "busybox:1.36", "command": ["sleep", "3600"]}],
            },
        },
    },
)

pulumi.export("name", deployment.metadata["name"])
