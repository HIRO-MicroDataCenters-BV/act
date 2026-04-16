"""
Example Pulumi program: nginx Deployment on Kubernetes.

Intentional violation: no securityContext.runAsNonRoot — container may run as root.
ACT will capture this in the mock output so a rule can flag it.
"""
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
                        # securityContext intentionally absent — violation
                    }
                ]
            },
        },
    },
)

pulumi.export("name", deployment.metadata["name"])
