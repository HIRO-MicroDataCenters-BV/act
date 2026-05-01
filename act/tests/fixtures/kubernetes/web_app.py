import pulumi
from pulumi_kubernetes.apps.v1 import Deployment
from pulumi_kubernetes.core.v1 import ConfigMap, Service

config = ConfigMap(
    "app-config",
    data={
        "APP_ENV": "production",
        "LOG_LEVEL": "info",
    },
)

deployment = Deployment(
    "web-app",
    spec={
        "replicas": 3,
        "selector": {"matchLabels": {"app": "web-app"}},
        "template": {
            "metadata": {"labels": {"app": "web-app"}},
            "spec": {
                "containers": [
                    {
                        "name": "api",
                        "image": "myapp/api:1.2.0",
                        "ports": [{"containerPort": 8080}],
                        "securityContext": {
                            "runAsNonRoot": True,
                            "readOnlyRootFilesystem": True,
                        },
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "128Mi"},
                            "limits": {"cpu": "500m", "memory": "512Mi"},
                        },
                    },
                    {
                        "name": "sidecar-logger",
                        "image": "myapp/logger:0.9.1",
                        # missing securityContext — violation
                    },
                ],
            },
        },
    },
)

service = Service(
    "web-app-svc",
    spec={
        "selector": {"app": "web-app"},
        "ports": [{"port": 80, "targetPort": 8080}],
        "type": "ClusterIP",
    },
)

pulumi.export("service", service.metadata["name"])
pulumi.export("deployment", deployment.metadata["name"])
