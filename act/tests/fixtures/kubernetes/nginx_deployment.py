import pulumi
from pulumi_kubernetes.apps.v1 import Deployment

deployment = Deployment(
    "nginx",
    spec={
        "replicas": 2,
        "selector": {"matchLabels": {"app": "nginx"}},
        "template": {
            "metadata": {"labels": {"app": "nginx"}},
            "spec": {
                "containers": [
                    {
                        "name": "nginx",
                        "image": "nginx:1.25",
                        "securityContext": {"runAsNonRoot": True},
                    }
                ]
            },
        },
    },
)

pulumi.export("name", deployment.metadata["name"])
