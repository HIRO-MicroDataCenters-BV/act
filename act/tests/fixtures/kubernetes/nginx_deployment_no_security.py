import pulumi
from pulumi_kubernetes.apps.v1 import Deployment

deployment = Deployment(
    "nginx",
    spec={
        "replicas": 1,
        "selector": {"matchLabels": {"app": "nginx"}},
        "template": {
            "metadata": {"labels": {"app": "nginx"}},
            "spec": {
                "containers": [
                    {
                        "name": "nginx",
                        "image": "nginx:latest",
                        # no securityContext — violation
                    }
                ]
            },
        },
    },
)
