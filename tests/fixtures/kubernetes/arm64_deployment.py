import pulumi
from pulumi_kubernetes.apps.v1 import Deployment

# Minimal arm64-targeted workload for the reproducibility runtime demo: the pause
# image is tiny, multi-arch, and reaches steady state near-instantly.
deployment = Deployment(
    "pause",
    spec={
        "replicas": 1,
        "selector": {"matchLabels": {"app": "pause"}},
        "template": {
            "metadata": {"labels": {"app": "pause"}},
            "spec": {
                "nodeSelector": {"kubernetes.io/arch": "arm64"},
                "containers": [{"name": "pause", "image": "registry.k8s.io/pause:3.9"}],
            },
        },
    },
)

pulumi.export("name", deployment.metadata["name"])
